"""Single-currency books (KRW book and USD book are always separate objects).

A ``Book`` only accepts instruments, cash and prices in its own currency.
Buy fees and taxes are added to the cost basis (average cost includes costs);
sell fees and taxes reduce realized P&L. Short selling and negative cash are
not allowed (no margin, PLAN.md 2.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from boututln.domain.enums import Side
from boututln.domain.market import Instrument
from boututln.domain.money import Currency, CurrencyMismatchError, FxRate, Money, to_decimal


class LedgerError(ValueError):
    pass


class InsufficientCashError(LedgerError):
    pass


class InsufficientQuantityError(LedgerError):
    pass


@dataclass(frozen=True, slots=True)
class Trade:
    instrument: Instrument
    side: Side
    quantity: Decimal
    price: Money
    fees: Money
    taxes: Money
    executed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", to_decimal(self.quantity))
        if self.quantity <= 0:
            raise LedgerError("quantity must be positive")
        if self.price.amount <= 0:
            raise LedgerError("price must be positive")
        if self.fees.is_negative() or self.taxes.is_negative():
            raise LedgerError("fees and taxes must not be negative")
        cur = self.instrument.currency
        for name, m in (("price", self.price), ("fees", self.fees), ("taxes", self.taxes)):
            if m.currency is not cur:
                raise CurrencyMismatchError(
                    f"{name} currency {m.currency.value} != instrument currency {cur.value}"
                )
        if self.executed_at.tzinfo is None:
            raise LedgerError("executed_at must be timezone-aware")

    @property
    def gross(self) -> Money:
        return self.price * self.quantity


@dataclass(slots=True)
class Position:
    instrument: Instrument
    quantity: Decimal
    cost_basis: Money  # total cost including buy fees/taxes

    @property
    def average_cost(self) -> Money:
        if self.quantity == 0:
            return Money.zero(self.cost_basis.currency)
        return self.cost_basis / self.quantity

    def market_value(self, price: Money) -> Money:
        return price * self.quantity

    def unrealized_pnl(self, price: Money) -> Money:
        return self.market_value(price) - self.cost_basis


@dataclass(slots=True)
class Book:
    currency: Currency
    cash: Money = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: Money = field(init=False)
    total_fees: Money = field(init=False)
    total_taxes: Money = field(init=False)
    trades: list[Trade] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = Money.zero(self.currency)
        self.realized_pnl = Money.zero(self.currency)
        self.total_fees = Money.zero(self.currency)
        self.total_taxes = Money.zero(self.currency)

    def _require_currency(self, m: Money) -> None:
        if m.currency is not self.currency:
            raise CurrencyMismatchError(
                f"{self.currency.value} book cannot accept {m.currency.value} amount"
            )

    def deposit(self, amount: Money) -> None:
        self._require_currency(amount)
        if amount.amount <= 0:
            raise LedgerError("deposit must be positive")
        self.cash = self.cash + amount

    def withdraw(self, amount: Money) -> None:
        self._require_currency(amount)
        if amount.amount <= 0:
            raise LedgerError("withdrawal must be positive")
        if amount > self.cash:
            raise InsufficientCashError("withdrawal exceeds cash")
        self.cash = self.cash - amount

    def apply(self, trade: Trade) -> Money:
        """Apply a trade; return realized P&L of this trade (zero for buys)."""
        if trade.instrument.currency is not self.currency:
            raise CurrencyMismatchError(
                f"{self.currency.value} book cannot hold {trade.instrument.key}"
            )
        if trade.side is Side.BUY:
            realized = self._buy(trade)
        else:
            realized = self._sell(trade)
        self.total_fees = self.total_fees + trade.fees
        self.total_taxes = self.total_taxes + trade.taxes
        self.trades.append(trade)
        return realized

    def _buy(self, t: Trade) -> Money:
        total = t.gross + t.fees + t.taxes
        if total > self.cash:
            raise InsufficientCashError(f"need {total}, have {self.cash}")
        self.cash = self.cash - total
        pos = self.positions.get(t.instrument.key)
        if pos is None:
            self.positions[t.instrument.key] = Position(t.instrument, t.quantity, total)
        else:
            pos.quantity += t.quantity
            pos.cost_basis = pos.cost_basis + total
        return Money.zero(self.currency)

    def _sell(self, t: Trade) -> Money:
        pos = self.positions.get(t.instrument.key)
        if pos is None or pos.quantity < t.quantity:
            raise InsufficientQuantityError(f"cannot sell {t.quantity} of {t.instrument.key}")
        cost_removed = pos.average_cost * t.quantity
        proceeds = t.gross - t.fees - t.taxes
        realized = proceeds - cost_removed
        pos.quantity -= t.quantity
        pos.cost_basis = pos.cost_basis - cost_removed
        if pos.quantity == 0:
            del self.positions[t.instrument.key]
        self.cash = self.cash + proceeds
        self.realized_pnl = self.realized_pnl + realized
        return realized

    def quantity(self, instrument: Instrument) -> Decimal:
        pos = self.positions.get(instrument.key)
        return pos.quantity if pos else Decimal("0")

    def market_value(self, prices: dict[str, Money]) -> Money:
        total = Money.zero(self.currency)
        for key, pos in self.positions.items():
            price = prices.get(key)
            if price is None:
                raise LedgerError(f"missing price for {key}")
            self._require_currency(price)
            total = total + pos.market_value(price)
        return total

    def cost_basis(self) -> Money:
        total = Money.zero(self.currency)
        for pos in self.positions.values():
            total = total + pos.cost_basis
        return total

    def unrealized_pnl(self, prices: dict[str, Money]) -> Money:
        return self.market_value(prices) - self.cost_basis()

    def nav(self, prices: dict[str, Money]) -> Money:
        return self.cash + self.market_value(prices)


@dataclass(frozen=True, slots=True)
class ConsolidatedValuation:
    """Report-only total in KRW. The native books are never modified by this."""

    krw_book_nav: Money
    usd_book_nav: Money
    usd_book_nav_in_krw: Money
    total_krw: Money
    fx: FxRate


def consolidate(krw_nav: Money, usd_nav: Money, fx: FxRate) -> ConsolidatedValuation:
    if krw_nav.currency is not Currency.KRW or usd_nav.currency is not Currency.USD:
        raise CurrencyMismatchError("consolidate expects a KRW nav and a USD nav")
    if not (fx.base is Currency.USD and fx.quote is Currency.KRW):
        raise CurrencyMismatchError("consolidate expects a USD/KRW rate")
    usd_in_krw = fx.convert(usd_nav)
    return ConsolidatedValuation(krw_nav, usd_nav, usd_in_krw, krw_nav + usd_in_krw, fx)
