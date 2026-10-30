"""Fee and tax model with effective dates (docs/backtesting-design.md 4.1).

Rates come from configuration, never from code constants, because most of them
are not officially confirmed yet. Each rule carries a ``verified`` flag so
reports can show which numbers are assumptions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boututln.domain.enums import Side
from boututln.domain.market import Market
from boututln.domain.money import Money, to_decimal


class FeeKind(StrEnum):
    COMMISSION = "commission"  # broker commission, % of gross
    TAX = "tax"  # transaction tax, % of gross
    REGULATORY = "regulatory"  # e.g. SEC fee (% of gross)
    PER_SHARE = "per_share"  # e.g. FINRA TAF (amount per share, optional cap)


class FeeRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    market: Market
    kind: FeeKind
    sides: list[Side]
    rate: Decimal = Field(description="fraction of gross, or amount per share for per_share")
    cap: Decimal | None = None
    effective_from: date
    effective_to: date | None = None
    verified: bool = False
    source: str = ""

    @field_validator("rate", "cap", mode="before")
    @classmethod
    def _no_float(cls, v: object) -> object:
        return None if v is None else to_decimal(v)


class FeeBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    commission: Money
    taxes: Money
    unverified_rules: list[str]

    @property
    def fees(self) -> Money:
        return self.commission


class FeeSchedule:
    def __init__(self, rules: list[FeeRule]) -> None:
        self._rules = list(rules)

    def applicable(self, market: Market, side: Side, on: date) -> list[FeeRule]:
        return [
            r
            for r in self._rules
            if r.market is market
            and side in r.sides
            and r.effective_from <= on
            and (r.effective_to is None or on < r.effective_to)
        ]

    def calculate(
        self, market: Market, side: Side, quantity: Decimal, price: Money, on: date
    ) -> FeeBreakdown:
        if price.currency is not market.currency:
            raise ValueError("price currency does not match market")
        gross = price * quantity
        commission = Money.zero(price.currency)
        taxes = Money.zero(price.currency)
        unverified: list[str] = []
        for r in self.applicable(market, side, on):
            if r.kind is FeeKind.PER_SHARE:
                amount = Money(r.rate * quantity, price.currency)
                if r.cap is not None and amount.amount > r.cap:
                    amount = Money(r.cap, price.currency)
            else:
                amount = gross * r.rate
            if r.kind is FeeKind.TAX:
                taxes = taxes + amount
            else:
                commission = commission + amount
            if not r.verified:
                unverified.append(r.name)
        return FeeBreakdown(
            commission=commission.quantized(), taxes=taxes.quantized(), unverified_rules=unverified
        )
