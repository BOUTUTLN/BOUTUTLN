"""Exchange calendars for KRX and NYSE (holidays, DST, half days).

Wraps ``exchange_calendars`` (Apache-2.0). All returned datetimes are UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Protocol

import pandas as pd

from boututln.domain.market import Market

_CODES = {Market.KR: "XKRX", Market.US: "XNYS"}


@dataclass(frozen=True, slots=True)
class Session:
    market: Market
    day: date
    open_utc: datetime
    close_utc: datetime


class MarketCalendar(Protocol):
    def is_trading_day(self, market: Market, day: date) -> bool: ...
    def session(self, market: Market, day: date) -> Session: ...
    def previous_trading_day(self, market: Market, day: date) -> date: ...
    def trading_days(self, market: Market, start: date, end: date) -> list[date]: ...


@lru_cache(maxsize=4)
def _cal(code: str):  # type: ignore[no-untyped-def]
    import exchange_calendars as xc

    return xc.get_calendar(code)


class ExchangeCalendar:
    def is_trading_day(self, market: Market, day: date) -> bool:
        return bool(_cal(_CODES[market]).is_session(pd.Timestamp(day)))

    def session(self, market: Market, day: date) -> Session:
        cal = _cal(_CODES[market])
        ts = pd.Timestamp(day)
        if not cal.is_session(ts):
            raise ValueError(f"{market.value} is closed on {day}")
        return Session(
            market,
            day,
            cal.session_open(ts).to_pydatetime(),
            cal.session_close(ts).to_pydatetime(),
        )

    def previous_trading_day(self, market: Market, day: date) -> date:
        cal = _cal(_CODES[market])
        ts = pd.Timestamp(day)
        prev = cal.date_to_session(ts, direction="previous")
        if prev == ts:
            prev = cal.previous_session(ts)
        return prev.date()  # type: ignore[no-any-return]

    def trading_days(self, market: Market, start: date, end: date) -> list[date]:
        cal = _cal(_CODES[market])
        return [s.date() for s in cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))]


class StaticCalendar:
    """Deterministic calendar for tests: explicit set of trading days."""

    def __init__(self, days: dict[Market, set[date]]) -> None:
        self._days = days

    def is_trading_day(self, market: Market, day: date) -> bool:
        return day in self._days.get(market, set())

    def session(self, market: Market, day: date) -> Session:
        if not self.is_trading_day(market, day):
            raise ValueError("closed")
        tz = market.timezone
        open_local = datetime(day.year, day.month, day.day, 9, 0, tzinfo=tz)
        close_local = datetime(day.year, day.month, day.day, 15, 30, tzinfo=tz)
        from datetime import UTC

        return Session(market, day, open_local.astimezone(UTC), close_local.astimezone(UTC))

    def previous_trading_day(self, market: Market, day: date) -> date:
        earlier = sorted(d for d in self._days.get(market, set()) if d < day)
        if not earlier:
            raise ValueError("no earlier trading day")
        return earlier[-1]

    def trading_days(self, market: Market, start: date, end: date) -> list[date]:
        return sorted(d for d in self._days.get(market, set()) if start <= d <= end)
