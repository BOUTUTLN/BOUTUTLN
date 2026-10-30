"""Data provider interface and the normalized bar model."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from boututln.domain.market import Market, check_market_currency
from boututln.domain.money import Currency, to_decimal


class Bar(BaseModel):
    """One OHLCV bar. Prices are Decimal; times are timezone-aware UTC.

    ``available_at`` is the earliest time the system could have known this
    bar; decisions made before it must not use it (backtesting-design 1.5).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    market: Market
    symbol: str
    timeframe: Literal["1d", "1m"]
    bar_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    currency: Currency
    source: str
    available_at: datetime
    ingested_at: datetime

    @field_validator("open", "high", "low", "close", "volume", mode="before")
    @classmethod
    def _decimal(cls, v: object) -> Decimal:
        return to_decimal(v)

    @field_validator("bar_time", "available_at", "ingested_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return v

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("empty symbol")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> Bar:
        check_market_currency(self.market, self.currency)
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("prices must be positive")
        if self.volume < 0:
            raise ValueError("volume must not be negative")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high is below open/close/low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low is above open/close/high")
        if self.available_at < self.bar_time:
            raise ValueError("available_at cannot precede bar_time")
        return self


class DataProvider(Protocol):
    """Implemented by each data source. Must go through the ProviderRegistry."""

    name: str

    def daily_bars(self, market: Market, symbol: str, start: date, end: date) -> list[Bar]: ...
