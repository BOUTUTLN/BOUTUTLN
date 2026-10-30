from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from boututln.domain.enums import Side
from boututln.domain.market import Instrument, Market, check_market_currency
from boututln.domain.money import Currency, CurrencyMismatchError, FxRate, Money, to_decimal
from boututln.portfolio.fees import FeeKind, FeeRule, FeeSchedule
from boututln.portfolio.ledger import (
    Book,
    InsufficientCashError,
    InsufficientQuantityError,
    Trade,
    consolidate,
)

T = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
SAMSUNG = Instrument(Market.KR, "005930")
SPY = Instrument(Market.US, "SPY")


def krw(x: str | int) -> Money:
    return Money.of(x, Currency.KRW)


def usd(x: str | int) -> Money:
    return Money.of(x, Currency.USD)


# --- Decimal and currency safety ------------------------------------------------


def test_float_is_rejected_everywhere() -> None:
    with pytest.raises(TypeError):
        to_decimal(0.1)
    with pytest.raises(TypeError):
        Money(0.1, Currency.KRW)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        krw(100) * 1.5


def test_decimal_arithmetic_is_exact() -> None:
    total = usd("0.10") + usd("0.20")
    assert total.amount == Decimal("0.30")
    assert (usd("10") / 3).quantized().amount == Decimal("3.33")
    assert krw("1000.5").quantized().amount == Decimal("1001")  # half-up


def test_krw_usd_never_mix() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = krw(1000) + usd(1)
    with pytest.raises(CurrencyMismatchError):
        _ = krw(1000) < usd(1)
    with pytest.raises(TypeError):
        _ = krw(1000) * krw(2)


def test_market_currency_mapping() -> None:
    check_market_currency(Market.KR, Currency.KRW)
    check_market_currency(Market.US, Currency.USD)
    with pytest.raises(ValueError):
        check_market_currency(Market.KR, Currency.USD)


def test_fx_is_explicit_and_directional() -> None:
    fx = FxRate(Currency.USD, Currency.KRW, Decimal("1400"), T, "test")
    assert fx.convert(usd(10)) == krw(14000)
    assert fx.convert(krw(14000)) == usd(10)
    with pytest.raises(ValueError):
        FxRate(Currency.USD, Currency.USD, Decimal("1"), T, "x")
    with pytest.raises(ValueError):
        FxRate(Currency.USD, Currency.KRW, Decimal("1400"), datetime(2026, 1, 1), "naive")


# --- Books -------------------------------------------------------------------


def buy(inst: Instrument, qty: str, price: Money, fee: Money | None = None) -> Trade:
    z = Money.zero(price.currency)
    return Trade(inst, Side.BUY, Decimal(qty), price, fee or z, z, T)


def sell(
    inst: Instrument, qty: str, price: Money, fee: Money | None = None, tax: Money | None = None
) -> Trade:
    z = Money.zero(price.currency)
    return Trade(inst, Side.SELL, Decimal(qty), price, fee or z, tax or z, T)


def test_average_cost_includes_buy_fees() -> None:
    b = Book(Currency.KRW)
    b.deposit(krw(2_000_000))
    b.apply(buy(SAMSUNG, "10", krw(70000), krw(100)))
    b.apply(buy(SAMSUNG, "10", krw(72000), krw(100)))
    pos = b.positions[SAMSUNG.key]
    assert pos.quantity == Decimal(20)
    assert pos.cost_basis == krw(1_420_200)  # 700,000 + 720,000 + fees 200
    assert pos.average_cost == krw("71010")
    assert b.cash == krw(2_000_000 - 1_420_200)
    assert b.total_fees == krw(200)


def test_realized_and_unrealized_pnl() -> None:
    b = Book(Currency.KRW)
    b.deposit(krw(2_000_000))
    b.apply(buy(SAMSUNG, "20", krw(70000)))  # cost 1,400,000, avg 70,000
    realized = b.apply(sell(SAMSUNG, "5", krw(75000), krw(50), krw(750)))
    # proceeds 375,000 - 800 = 374,200; cost removed 350,000
    assert realized == krw(24200)
    assert b.realized_pnl == krw(24200)
    assert b.quantity(SAMSUNG) == Decimal(15)
    prices = {SAMSUNG.key: krw(72000)}
    assert b.market_value(prices) == krw(1_080_000)
    assert b.unrealized_pnl(prices) == krw(1_080_000 - 1_050_000)
    assert b.cash == krw(2_000_000 - 1_400_000 + 374_200)
    assert b.total_taxes == krw(750)


def test_books_reject_foreign_instruments_and_amounts() -> None:
    kb = Book(Currency.KRW)
    kb.deposit(krw(1_000_000))
    with pytest.raises(CurrencyMismatchError):
        kb.deposit(usd(100))
    with pytest.raises(CurrencyMismatchError):
        kb.apply(buy(SPY, "1", usd(500)))
    with pytest.raises(CurrencyMismatchError):
        Trade(SAMSUNG, Side.BUY, Decimal(1), usd(10), usd(0), usd(0), T)


def test_no_negative_cash_or_short_selling() -> None:
    ub = Book(Currency.USD)
    ub.deposit(usd(1000))
    with pytest.raises(InsufficientCashError):
        ub.apply(buy(SPY, "3", usd(400)))
    with pytest.raises(InsufficientQuantityError):
        ub.apply(sell(SPY, "1", usd(400)))
    assert ub.cash == usd(1000)  # failed trades change nothing


def test_consolidation_is_report_only() -> None:
    fx = FxRate(Currency.USD, Currency.KRW, Decimal("1400"), T, "test")
    c = consolidate(krw(1_000_000), usd(1000), fx)
    assert c.total_krw == krw(2_400_000)
    assert c.usd_book_nav == usd(1000)  # native book value unchanged
    with pytest.raises(CurrencyMismatchError):
        consolidate(usd(1), usd(1), fx)


# --- Fees and taxes ------------------------------------------------------------


def test_fee_schedule_effective_dates_and_caps() -> None:
    sched = FeeSchedule(
        [
            FeeRule(
                name="kr_tax_2026",
                market=Market.KR,
                kind=FeeKind.TAX,
                sides=[Side.SELL],
                rate=Decimal("0.0020"),
                effective_from=date(2026, 1, 1),
                verified=False,
            ),
            FeeRule(
                name="kr_tax_2025",
                market=Market.KR,
                kind=FeeKind.TAX,
                sides=[Side.SELL],
                rate=Decimal("0.0015"),
                effective_from=date(2025, 1, 1),
                effective_to=date(2026, 1, 1),
            ),
            FeeRule(
                name="finra_taf",
                market=Market.US,
                kind=FeeKind.PER_SHARE,
                sides=[Side.SELL],
                rate=Decimal("0.000195"),
                cap=Decimal("9.79"),
                effective_from=date(2026, 1, 1),
                verified=True,
            ),
        ]
    )
    new = sched.calculate(Market.KR, Side.SELL, Decimal(10), krw(70000), date(2026, 10, 30))
    assert new.taxes == krw(1400)
    assert new.unverified_rules == ["kr_tax_2026"]
    old = sched.calculate(Market.KR, Side.SELL, Decimal(10), krw(70000), date(2025, 6, 1))
    assert old.taxes == krw(1050)
    assert sched.calculate(
        Market.KR, Side.BUY, Decimal(10), krw(70000), date(2026, 6, 1)
    ).taxes == krw(0)
    capped = sched.calculate(Market.US, Side.SELL, Decimal(100000), usd(1), date(2026, 6, 1))
    assert capped.commission == usd("9.79")
    with pytest.raises(TypeError):
        FeeRule(
            name="x",
            market=Market.KR,
            kind=FeeKind.TAX,
            sides=[Side.SELL],
            rate=0.002,  # type: ignore[arg-type]
            effective_from=date(2026, 1, 1),
        )
