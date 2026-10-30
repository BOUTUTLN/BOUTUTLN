"""Currency-safe money arithmetic.

All monetary amounts are ``Decimal``. ``float`` is rejected everywhere, and
amounts in different currencies can never be added, subtracted or compared.
Conversion between currencies only happens through an explicit ``FxRate``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum


class Currency(StrEnum):
    KRW = "KRW"
    USD = "USD"


# Minor units used when an amount is settled or displayed.
_MINOR_UNITS: dict[Currency, Decimal] = {
    Currency.KRW: Decimal("1"),
    Currency.USD: Decimal("0.01"),
}


class CurrencyMismatchError(ValueError):
    """Raised when amounts in different currencies are combined."""


def to_decimal(value: object) -> Decimal:
    """Convert ``value`` to Decimal, refusing floats and non-finite values."""
    if isinstance(value, bool):
        raise TypeError("bool is not a valid amount")
    if isinstance(value, float):
        raise TypeError("float is not allowed for financial amounts; use Decimal or str")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal number: {value!r}") from exc
    else:
        raise TypeError(f"unsupported amount type: {type(value).__name__}")
    if not result.is_finite():
        raise ValueError("amount must be finite")
    return result


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        if not isinstance(self.currency, Currency):
            object.__setattr__(self, "currency", Currency(self.currency))

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(Decimal("0"), currency)

    @classmethod
    def of(cls, amount: object, currency: Currency | str) -> Money:
        return cls(to_decimal(amount), Currency(currency))

    def _check(self, other: object) -> Money:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency is not self.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency.value} with {other.currency.value}"
            )
        return other

    def __add__(self, other: object) -> Money:
        o = self._check(other)
        return Money(self.amount + o.amount, self.currency)

    def __sub__(self, other: object) -> Money:
        o = self._check(other)
        return Money(self.amount - o.amount, self.currency)

    def __mul__(self, factor: object) -> Money:
        if isinstance(factor, Money):
            raise TypeError("cannot multiply Money by Money")
        return Money(self.amount * to_decimal(factor), self.currency)

    __rmul__ = __mul__

    def __truediv__(self, divisor: object) -> Money:
        if isinstance(divisor, Money):
            raise TypeError("use ratio() to divide Money by Money")
        d = to_decimal(divisor)
        if d == 0:
            raise ZeroDivisionError("division by zero")
        return Money(self.amount / d, self.currency)

    def ratio(self, other: Money) -> Decimal:
        o = self._check(other)
        if o.amount == 0:
            raise ZeroDivisionError("division by zero")
        return self.amount / o.amount

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: object) -> bool:
        return self.amount < self._check(other).amount

    def __le__(self, other: object) -> bool:
        return self.amount <= self._check(other).amount

    def __gt__(self, other: object) -> bool:
        return self.amount > self._check(other).amount

    def __ge__(self, other: object) -> bool:
        return self.amount >= self._check(other).amount

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_negative(self) -> bool:
        return self.amount < 0

    def quantized(self) -> Money:
        """Round to the currency's minor unit (KRW: 1, USD: 0.01), half-up."""
        return Money(
            self.amount.quantize(_MINOR_UNITS[self.currency], rounding=ROUND_HALF_UP),
            self.currency,
        )

    def __str__(self) -> str:
        return f"{self.amount} {self.currency.value}"


@dataclass(frozen=True, slots=True)
class FxRate:
    """Explicit exchange rate: 1 ``base`` = ``rate`` ``quote``."""

    base: Currency
    quote: Currency
    rate: Decimal
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate", to_decimal(self.rate))
        if self.base is self.quote:
            raise ValueError("base and quote currency must differ")
        if self.rate <= 0:
            raise ValueError("rate must be positive")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")

    def convert(self, money: Money) -> Money:
        if money.currency is self.base:
            return Money(money.amount * self.rate, self.quote)
        if money.currency is self.quote:
            return Money(money.amount / self.rate, self.base)
        raise CurrencyMismatchError(
            f"rate {self.base.value}/{self.quote.value} cannot convert {money.currency.value}"
        )
