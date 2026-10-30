from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from boututln.core.clock import FixedClock
from boututln.core.config import ProviderConfig
from boututln.core.errors import (
    ExternalCallBlockedError,
    OrderEndpointBlockedError,
    PaidServiceBlockedError,
    QuotaExceededError,
)
from boututln.core.market_calendar import ExchangeCalendar, StaticCalendar
from boututln.database import repositories as repo
from boututln.domain.market import Market
from boututln.providers.base import Bar
from boututln.providers.http_guard import GuardedTransport, check_url
from boututln.providers.registry import CostClass, ProviderRegistry, load_approvals
from boututln.providers.storage import ParquetBarStore
from boututln.providers.validation import (
    check_freshness,
    dedupe,
    find_duplicates,
    leaks_future,
    missing_sessions,
    parse_bars,
)

NOW = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)


def bar_row(
    day: date, close: str = "100", market: str = "KR", symbol: str = "005930", **over: Any
) -> dict[str, Any]:
    cal = ExchangeCalendar()
    sess = cal.session(Market(market), day)
    row: dict[str, Any] = dict(
        market=market,
        symbol=symbol,
        timeframe="1d",
        bar_time=sess.close_utc,
        open="99",
        high="101",
        low="98",
        close=close,
        volume="1000",
        currency="KRW" if market == "KR" else "USD",
        source="test",
        available_at=sess.close_utc + timedelta(minutes=30),
        ingested_at=NOW,
    )
    row.update(over)
    return row


def bars(days: list[date], **kw: Any) -> list[Bar]:
    return [Bar.model_validate(bar_row(d, **kw)) for d in days]


# --- format validation -----------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"close": 0.5},  # float price
        {"high": "97"},  # high below low/open
        {"currency": "USD"},  # wrong currency for KR
        {"bar_time": datetime(2026, 10, 29)},  # naive datetime
        {"open": "abc"},  # not a number
        {"volume": "-1"},  # negative volume
        {"extra_field": 1},  # unknown field
    ],
)
def test_invalid_rows_are_rejected_not_coerced(override: dict[str, Any]) -> None:
    rep = parse_bars([bar_row(date(2026, 10, 29), **override)])
    assert not rep.bars and len(rep.rejected) == 1


def test_lookahead_detection() -> None:
    b = bars([date(2026, 10, 29)])[0]
    assert leaks_future([b], b.bar_time) == [b]  # close not yet published 30 min later
    assert leaks_future([b], b.available_at) == []
    with pytest.raises(ValueError):
        Bar.model_validate(bar_row(date(2026, 10, 29), available_at=b.bar_time - timedelta(1)))


# --- duplicates ------------------------------------------------------------------


def test_duplicate_bars_detected_and_ignored_in_db(engine: Engine) -> None:
    bs = bars([date(2026, 10, 28), date(2026, 10, 29)])
    assert find_duplicates(bs + bs[:1]) and len(dedupe(bs + bs)) == 2
    with engine.begin() as c:
        assert repo.insert_bars(c, bs) == (2, 0)
        assert repo.insert_bars(c, bs) == (0, 2)
        latest = repo.latest_bar_times(c, Market.KR)
    assert latest["005930"].astimezone(Market.KR.timezone).date() == date(2026, 10, 29)


def test_parquet_store_roundtrip_dedupes(tmp_path: Path) -> None:
    store = ParquetBarStore(tmp_path / "raw")
    bs = bars([date(2026, 10, 28), date(2026, 10, 29)])
    store.write(bs)
    store.write(bs)
    back = store.read(Market.KR, "005930")
    assert back == bs
    assert back[0].close == Decimal("100")


# --- calendar, gaps and staleness ------------------------------------------------


def test_exchange_calendar_holidays_and_dst() -> None:
    cal = ExchangeCalendar()
    assert not cal.is_trading_day(Market.KR, date(2026, 9, 24))  # Chuseok
    assert not cal.is_trading_day(Market.KR, date(2026, 10, 9))  # Hangul day
    assert not cal.is_trading_day(Market.US, date(2026, 11, 26))  # Thanksgiving
    assert cal.session(Market.US, date(2026, 3, 6)).open_utc.hour == 14  # EST
    assert cal.session(Market.US, date(2026, 3, 9)).open_utc.hour == 13  # EDT
    assert cal.previous_trading_day(Market.KR, date(2026, 10, 12)) == date(2026, 10, 8)


def test_missing_sessions_found() -> None:
    cal = ExchangeCalendar()
    have = bars([date(2026, 10, 26), date(2026, 10, 28)])
    assert missing_sessions(have, cal, Market.KR, date(2026, 10, 26), date(2026, 10, 28)) == [
        date(2026, 10, 27)
    ]


def test_freshness_flags_stale_and_missing() -> None:
    cal = ExchangeCalendar()
    latest = {
        "005930": bars([date(2026, 10, 28)])[0].bar_time,
        "000660": bars([date(2026, 10, 29)], symbol="000660")[0].bar_time,
    }
    fr = check_freshness(Market.KR, date(2026, 10, 30), latest, ["005930", "000660", "035420"], cal)
    assert fr.expected_session == date(2026, 10, 29)
    assert fr.fresh_symbols == ["000660"]
    assert fr.stale_symbols == ["005930"] and fr.missing_symbols == ["035420"]
    assert not fr.is_fresh


def test_static_calendar_for_tests() -> None:
    cal = StaticCalendar({Market.KR: {date(2026, 1, 2), date(2026, 1, 5)}})
    assert cal.previous_trading_day(Market.KR, date(2026, 1, 5)) == date(2026, 1, 2)


# --- cost classes and quotas -----------------------------------------------------


def _reg(clock: FixedClock, approved: set[str] | None = None) -> ProviderRegistry:
    return ProviderRegistry(
        [
            ProviderConfig(name="kb", cost_class="unknown", hosts=["developer.kbsec.com"]),
            ProviderConfig(name="paid_api", cost_class="paid", hosts=["paid.example.com"]),
            ProviderConfig(
                name="dart",
                cost_class="free_limited",
                hosts=["opendart.fss.or.kr"],
                daily_limit=3,
                per_minute_limit=2,
            ),
        ],
        approved,
        clock,
    )


def test_paid_and_unknown_providers_blocked_without_approval(clock: FixedClock) -> None:
    r = _reg(clock)
    for name in ("kb", "paid_api", "not_registered"):
        with pytest.raises(PaidServiceBlockedError):
            r.acquire(name)
    assert {p.name for p in r.non_free()} == {"kb", "paid_api"}
    assert "developer.kbsec.com" not in r.allowed_hosts()
    assert CostClass.UNKNOWN.label_ko == "확인 필요"
    r2 = _reg(clock, {"kb"})
    r2.acquire("kb")
    assert "developer.kbsec.com" in r2.allowed_hosts()


def test_approval_file_requires_who_and_when(tmp_path: Path) -> None:
    f = tmp_path / "paid_services.toml"
    f.write_text(
        '[[approved]]\nprovider = "kb"\n\n[[approved]]\nprovider = "x"\n'
        'approved_by = "me"\napproved_on = "2026-11-01"\n',
        encoding="utf-8",
    )
    assert load_approvals(f) == {"x"}
    assert load_approvals(tmp_path / "missing.toml") == set()


def test_free_quota_hard_stop_and_daily_reset(clock: FixedClock) -> None:
    r = _reg(clock)
    r.acquire("dart")
    r.acquire("dart")
    with pytest.raises(QuotaExceededError):
        r.acquire("dart")  # per-minute
    clock.advance(timedelta(minutes=2))
    r.acquire("dart")
    clock.advance(timedelta(minutes=2))
    with pytest.raises(QuotaExceededError):
        r.acquire("dart")  # daily limit reached
    clock.advance(timedelta(minutes=5))
    with pytest.raises(QuotaExceededError):
        r.acquire("dart")  # stays stopped for the day
    clock.advance(timedelta(days=1))
    r.acquire("dart")
    assert r.usage_today("dart") == 1


# --- outbound guard --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.anthropic.com/v1/messages",
        "https://api.openai.com/v1/chat/completions",
        "https://ollama.com/api/chat",
        "https://generativelanguage.googleapis.com/v1/models",
    ],
)
def test_external_ai_hosts_always_blocked(url: str) -> None:
    with pytest.raises(ExternalCallBlockedError):
        check_url(httpx.URL(url), allowed_hosts=[httpx.URL(url).host])


def test_guard_blocks_unknown_hosts_and_order_paths() -> None:
    with pytest.raises(ExternalCallBlockedError):
        check_url(httpx.URL("https://evil.example.com/x"), allowed_hosts=["opendart.fss.or.kr"])
    with pytest.raises(OrderEndpointBlockedError):
        check_url(
            httpx.URL("https://developer.kbsec.com:32484/api/v1/ssam1802"),
            allowed_hosts=["developer.kbsec.com"],
        )
    with pytest.raises(ExternalCallBlockedError):
        check_url(
            httpx.URL("http://127.0.0.1:8080/"), allowed_hosts=[], allow_loopback_ports=[11434]
        )
    check_url(
        httpx.URL("http://127.0.0.1:11434/api/tags"), allowed_hosts=[], allow_loopback_ports=[11434]
    )


def test_guarded_transport_never_sends_blocked_request() -> None:
    sent: list[httpx.Request] = []
    inner = httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200))
    client = httpx.Client(transport=GuardedTransport(inner, ["opendart.fss.or.kr"]))
    with pytest.raises(ExternalCallBlockedError):
        client.get("https://api.openai.com/v1/models")
    client.get("https://opendart.fss.or.kr/api/list.json")
    assert [r.url.host for r in sent] == ["opendart.fss.or.kr"]
