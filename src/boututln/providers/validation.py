"""Data quality checks: parsing raw rows, duplicates, gaps, staleness, look-ahead."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from boututln.core.market_calendar import MarketCalendar
from boututln.domain.market import Market
from boututln.providers.base import Bar


@dataclass
class ParseReport:
    bars: list[Bar] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)  # (row index, reason)


def parse_bars(rows: Iterable[Mapping[str, Any]]) -> ParseReport:
    """Validate raw rows; invalid rows are rejected with a reason, never coerced."""
    rep = ParseReport()
    for i, row in enumerate(rows):
        try:
            rep.bars.append(Bar.model_validate(dict(row)))
        except (ValidationError, TypeError, ValueError) as exc:
            rep.rejected.append((i, str(exc).splitlines()[0]))
    return rep


def find_duplicates(bars: Iterable[Bar]) -> list[tuple[str, str, datetime]]:
    seen: set[tuple[str, str, str, datetime, str]] = set()
    dups: list[tuple[str, str, datetime]] = []
    for b in bars:
        key = (b.market.value, b.symbol, b.timeframe, b.bar_time, b.source)
        if key in seen:
            dups.append((b.market.value, b.symbol, b.bar_time))
        seen.add(key)
    return dups


def dedupe(bars: Iterable[Bar]) -> list[Bar]:
    out: dict[tuple[str, str, str, datetime, str], Bar] = {}
    for b in bars:
        out.setdefault((b.market.value, b.symbol, b.timeframe, b.bar_time, b.source), b)
    return list(out.values())


def missing_sessions(
    bars: Iterable[Bar], calendar: MarketCalendar, market: Market, start: date, end: date
) -> list[date]:
    """Trading days in [start, end] that have no daily bar (market-local date)."""
    have = {b.bar_time.astimezone(market.timezone).date() for b in bars if b.timeframe == "1d"}
    return [d for d in calendar.trading_days(market, start, end) if d not in have]


def leaks_future(bars: Iterable[Bar], decision_time: datetime) -> list[Bar]:
    """Bars that were not yet available at ``decision_time`` (look-ahead)."""
    return [b for b in bars if b.available_at > decision_time]


@dataclass(frozen=True)
class FreshnessResult:
    market: Market
    expected_session: date
    fresh_symbols: list[str]
    stale_symbols: list[str]
    missing_symbols: list[str]

    @property
    def is_fresh(self) -> bool:
        return not self.stale_symbols and not self.missing_symbols


def check_freshness(
    market: Market,
    run_day: date,
    latest_by_symbol: Mapping[str, datetime],
    required_symbols: Iterable[str],
    calendar: MarketCalendar,
) -> FreshnessResult:
    """Daily bars must reach the trading day before ``run_day`` (market-local)."""
    expected = calendar.previous_trading_day(market, run_day)
    fresh, stale, missing = [], [], []
    for sym in sorted({s.upper() for s in required_symbols}):
        ts = latest_by_symbol.get(sym)
        if ts is None:
            missing.append(sym)
        elif ts.astimezone(market.timezone).date() < expected:
            stale.append(sym)
        else:
            fresh.append(sym)
    return FreshnessResult(market, expected, fresh, stale, missing)
