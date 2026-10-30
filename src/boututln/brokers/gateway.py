"""Order gateway boundary.

Queries and orders are separate interfaces. The only order gateway that exists
in this build is the paper gateway, which writes to the local paper database.
There is no live gateway class to construct, so a mis-set flag cannot reach the
broker: asking for live raises ``LiveTradingLockedError``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import Connection

from boututln.core.errors import LiveTradingLockedError
from boututln.domain.enums import OrderType, Side, TradingMode
from boututln.domain.market import Market, check_market_currency
from boututln.domain.money import Money, to_decimal


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    intent_id: str
    account_id: int
    market: Market
    symbol: str
    side: Side
    order_type: OrderType = OrderType.LIMIT
    quantity: Decimal
    limit_price: Money

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v: object) -> Decimal:
        d = to_decimal(v)
        if d <= 0:
            raise ValueError("quantity must be positive")
        if d != d.to_integral_value():
            raise ValueError("fractional quantities are disabled (PLAN 2.2)")
        return d

    @model_validator(mode="after")
    def _currency(self) -> OrderIntent:
        check_market_currency(self.market, self.limit_price.currency)
        if self.limit_price.amount <= 0:
            raise ValueError("limit price must be positive")
        return self


class OrderGateway(Protocol):
    mode: TradingMode

    def submit(self, conn: Connection, intent: OrderIntent) -> int: ...


def build_order_gateway(mode: TradingMode) -> OrderGateway:
    if mode is TradingMode.LIVE:
        raise LiveTradingLockedError("live order gateway does not exist in this build (P0)")
    from boututln.paper_trading.gateway import PaperOrderGateway

    return PaperOrderGateway(mode)
