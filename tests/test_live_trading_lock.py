"""P0 most important requirement: no real order can be placed (PLAN 7)."""

from __future__ import annotations

import importlib.util
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from boututln.brokers.gateway import OrderIntent, build_order_gateway
from boututln.brokers.kb import endpoints as ep
from boututln.brokers.kb.client import KBQueryClient
from boututln.brokers.kb.transport import DisabledTransport
from boututln.core.config import PathsConfig
from boututln.core.errors import LiveTradingLockedError
from boututln.database import repositories as repo
from boututln.database import schema as s
from boututln.domain.enums import HaltLevel, Side, TradingMode
from boututln.domain.market import Market
from boututln.domain.money import Currency, Money
from boututln.paper_trading.gateway import DuplicateOrderIntentError, PaperOrderGateway
from boututln.risk.kill_switch import TradingHaltedError, set_level

SRC = Path(__file__).resolve().parents[1] / "src" / "boututln"
T = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)


def test_no_live_gateway_exists() -> None:
    with pytest.raises(LiveTradingLockedError):
        build_order_gateway(TradingMode.LIVE)
    with pytest.raises(LiveTradingLockedError):
        PaperOrderGateway(TradingMode.LIVE)
    assert importlib.util.find_spec("boututln.brokers.kb.orders") is None
    assert not any(SRC.rglob("*live*broker*.py"))


def test_no_live_database_path(tmp_path: Path) -> None:
    with pytest.raises(LiveTradingLockedError):
        PathsConfig(data_dir=tmp_path).db_path(TradingMode.LIVE)


def test_order_endpoint_codes_only_in_denylist() -> None:
    """Static check: order API codes appear nowhere except the endpoint denylist."""
    pattern = re.compile("|".join(sorted(ep.ORDER_ENDPOINT_CODES)), re.IGNORECASE)
    offenders = [
        p
        for p in SRC.rglob("*.py")
        if p.name != "endpoints.py" and pattern.search(p.read_text("utf-8"))
    ]
    assert offenders == []


def test_paper_order_path_never_touches_broker(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*a: object, **k: object) -> None:
        raise AssertionError("broker network path was reached from paper trading")

    monkeypatch.setattr(KBQueryClient, "_post", explode)
    monkeypatch.setattr(DisabledTransport, "post", explode)
    with engine.begin() as c:
        acc = repo.create_account(c, broker="SIM", alias="paper", mode=TradingMode.PAPER, now=T)
    gw = build_order_gateway(TradingMode.PAPER)
    intent = OrderIntent(
        intent_id="run1:KR:005930:buy:0",
        account_id=acc,
        market=Market.KR,
        symbol="005930",
        side=Side.BUY,
        quantity=Decimal(1),
        limit_price=Money.of(70000, "KRW"),
    )
    with engine.begin() as c:
        gw.submit(c, intent)
    with engine.connect() as c:
        row = c.execute(select(s.orders)).one()
    assert row.mode == "paper" and row.status == "ACKNOWLEDGED" and row.broker_order_no is None


def test_duplicate_intent_and_invalid_intents(engine: Engine) -> None:
    with engine.begin() as c:
        acc = repo.create_account(c, broker="SIM", alias="paper", mode=TradingMode.PAPER, now=T)
    gw = PaperOrderGateway()
    intent = OrderIntent(
        intent_id="dup",
        account_id=acc,
        market=Market.US,
        symbol="SPY",
        side=Side.BUY,
        quantity=Decimal(1),
        limit_price=Money.of("500", "USD"),
    )
    with engine.begin() as c:
        gw.submit(c, intent)
    with pytest.raises(DuplicateOrderIntentError):
        with engine.begin() as c:
            gw.submit(c, intent)
    with pytest.raises(ValueError):  # KRW price for a US stock
        OrderIntent(
            intent_id="x",
            account_id=acc,
            market=Market.US,
            symbol="SPY",
            side=Side.BUY,
            quantity=Decimal(1),
            limit_price=Money.of(700000, "KRW"),
        )
    with pytest.raises(ValueError):  # fractional shares disabled
        OrderIntent(
            intent_id="y",
            account_id=acc,
            market=Market.US,
            symbol="SPY",
            side=Side.BUY,
            quantity=Decimal("0.5"),
            limit_price=Money.of(500, "USD"),
        )


def test_kill_switch_blocks_without_liquidating(engine: Engine) -> None:
    with engine.begin() as c:
        acc = repo.create_account(c, broker="SIM", alias="paper", mode=TradingMode.PAPER, now=T)
        set_level(c, HaltLevel.L1, reason="daily loss limit", now=T, currency=Currency.KRW)
    gw = PaperOrderGateway()

    def intent(i: str, side: Side) -> OrderIntent:
        return OrderIntent(
            intent_id=i,
            account_id=acc,
            market=Market.KR,
            symbol="005930",
            side=side,
            quantity=Decimal(1),
            limit_price=Money.of(70000, "KRW"),
        )

    with pytest.raises(TradingHaltedError):
        with engine.begin() as c:
            gw.submit(c, intent("b1", Side.BUY))
    with engine.begin() as c:
        gw.submit(c, intent("s1", Side.SELL))  # L1 still allows sells
        set_level(c, HaltLevel.L3, reason="manual /halt", now=T)
    with pytest.raises(TradingHaltedError):
        with engine.begin() as c:
            gw.submit(c, intent("s2", Side.SELL))
    with engine.connect() as c:
        sides = [r.side for r in c.execute(select(s.orders.c.side)).all()]
        events = c.execute(select(s.risk_events.c.level)).scalars().all()
    assert sides == ["sell"]  # nothing was sold automatically
    assert events == ["L1", "L3"]
