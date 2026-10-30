"""Markets, instruments and their fixed currency mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from boututln.domain.money import Currency


class Market(StrEnum):
    KR = "KR"
    US = "US"

    @property
    def currency(self) -> Currency:
        return MARKET_CURRENCY[self]

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(MARKET_TIMEZONE[self])


MARKET_CURRENCY: dict[Market, Currency] = {Market.KR: Currency.KRW, Market.US: Currency.USD}
MARKET_TIMEZONE: dict[Market, str] = {Market.KR: "Asia/Seoul", Market.US: "America/New_York"}


@dataclass(frozen=True, slots=True)
class Instrument:
    market: Market
    symbol: str

    def __post_init__(self) -> None:
        sym = self.symbol.strip().upper()
        if not sym:
            raise ValueError("symbol must not be empty")
        object.__setattr__(self, "symbol", sym)
        if not isinstance(self.market, Market):
            object.__setattr__(self, "market", Market(self.market))

    @property
    def currency(self) -> Currency:
        return self.market.currency

    @property
    def key(self) -> str:
        return f"{self.market.value}:{self.symbol}"


def check_market_currency(market: Market | str, currency: Currency | str) -> None:
    """Raise ValueError if ``currency`` is not the trading currency of ``market``."""
    m = Market(market)
    c = Currency(currency)
    if m.currency is not c:
        raise ValueError(f"market {m.value} trades in {m.currency.value}, not {c.value}")
