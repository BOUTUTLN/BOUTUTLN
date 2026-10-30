"""Data access functions (SQLAlchemy Core).

Every money value crosses this boundary as ``Money`` and is checked against the
row's currency here, in addition to the CHECK constraints in the schema.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Table, func, select
from sqlalchemy.dialects import postgresql, sqlite

from boututln.core.masking import mask_text
from boututln.database import schema as s
from boututln.domain.enums import TradingMode
from boututln.domain.market import Instrument, Market, check_market_currency
from boututln.domain.money import Currency, CurrencyMismatchError, Money
from boututln.portfolio.ledger import Book, Position


def insert_ignore_duplicates(
    conn: Connection, table: Table, rows: Sequence[Mapping[str, Any]]
) -> int:
    """Insert rows, silently skipping rows that violate a unique constraint.

    Returns the number of rows actually inserted.
    """
    if not rows:
        return 0
    name = conn.dialect.name
    if name == "sqlite":
        stmt: Any = sqlite.insert(table).on_conflict_do_nothing()
    elif name == "postgresql":
        stmt = postgresql.insert(table).on_conflict_do_nothing()
    else:  # pragma: no cover - other backends are not supported
        raise NotImplementedError(name)
    before = conn.execute(select(func.count()).select_from(table)).scalar_one()
    conn.execute(stmt, list(rows))
    after = conn.execute(select(func.count()).select_from(table)).scalar_one()
    return int(after - before)


# --- accounts -------------------------------------------------------------


def create_account(
    conn: Connection, *, broker: str, alias: str, mode: TradingMode, now: datetime
) -> int:
    if mode is TradingMode.LIVE:
        raise ValueError("live accounts cannot be created in this build")
    res = conn.execute(
        s.accounts.insert().values(broker=broker, alias=alias, mode=mode.value, created_at=now)
    )
    pk = res.inserted_primary_key
    assert pk is not None
    return int(pk[0])


# --- cash and holdings ----------------------------------------------------


def set_cash(conn: Connection, account_id: int, cash: Money, now: datetime) -> None:
    existing = conn.execute(
        select(s.cash_balances.c.id).where(
            s.cash_balances.c.account_id == account_id,
            s.cash_balances.c.currency == cash.currency.value,
        )
    ).first()
    values = {"amount": cash.amount, "updated_at": now}
    if existing:
        conn.execute(s.cash_balances.update().where(s.cash_balances.c.id == existing[0]), values)
    else:
        conn.execute(
            s.cash_balances.insert().values(
                account_id=account_id, currency=cash.currency.value, **values
            )
        )


def get_cash(conn: Connection, account_id: int, currency: Currency) -> Money:
    row = conn.execute(
        select(s.cash_balances.c.amount, s.cash_balances.c.currency).where(
            s.cash_balances.c.account_id == account_id,
            s.cash_balances.c.currency == currency.value,
        )
    ).first()
    if row is None:
        return Money.zero(currency)
    if row.currency != currency.value:  # defensive: should be impossible
        raise CurrencyMismatchError("stored cash currency mismatch")
    return Money(row.amount, currency)


def save_book(conn: Connection, account_id: int, book: Book, now: datetime) -> None:
    """Persist one currency book. Rows of the other currency are untouched."""
    set_cash(conn, account_id, book.cash, now)
    h = s.holdings
    keep: set[tuple[str, str]] = set()
    for pos in book.positions.values():
        inst = pos.instrument
        if inst.currency is not book.currency or pos.cost_basis.currency is not book.currency:
            raise CurrencyMismatchError("position currency does not match book")
        keep.add((inst.market.value, inst.symbol))
        values = {
            "currency": book.currency.value,
            "quantity": pos.quantity,
            "cost_basis": pos.cost_basis.amount,
            "updated_at": now,
        }
        row = conn.execute(
            select(h.c.id).where(
                h.c.account_id == account_id,
                h.c.market == inst.market.value,
                h.c.symbol == inst.symbol,
            )
        ).first()
        if row:
            conn.execute(h.update().where(h.c.id == row[0]), values)
        else:
            conn.execute(
                h.insert().values(
                    account_id=account_id,
                    market=inst.market.value,
                    symbol=inst.symbol,
                    realized_pnl=Decimal("0"),
                    **values,
                )
            )
    for row in conn.execute(
        select(h.c.id, h.c.market, h.c.symbol).where(
            h.c.account_id == account_id, h.c.currency == book.currency.value
        )
    ).all():
        if (row.market, row.symbol) not in keep:
            conn.execute(h.delete().where(h.c.id == row.id))
    set_state(
        conn,
        f"book_realized:{account_id}:{book.currency.value}",
        {
            "realized_pnl": str(book.realized_pnl.amount),
            "total_fees": str(book.total_fees.amount),
            "total_taxes": str(book.total_taxes.amount),
        },
        now,
    )


def load_book(conn: Connection, account_id: int, currency: Currency) -> Book:
    book = Book(currency)
    book.cash = get_cash(conn, account_id, currency)
    h = s.holdings
    for row in conn.execute(
        select(h).where(h.c.account_id == account_id, h.c.currency == currency.value)
    ).all():
        inst = Instrument(Market(row.market), row.symbol)
        check_market_currency(inst.market, currency)
        book.positions[inst.key] = Position(inst, row.quantity, Money(row.cost_basis, currency))
    st = get_state(conn, f"book_realized:{account_id}:{currency.value}")
    if st:
        book.realized_pnl = Money.of(st["realized_pnl"], currency)
        book.total_fees = Money.of(st["total_fees"], currency)
        book.total_taxes = Money.of(st["total_taxes"], currency)
    return book


def record_external_exchange(
    conn: Connection,
    account_id: int,
    *,
    sold: Money,
    bought: Money,
    executed_at: datetime,
) -> int:
    """Record a currency exchange the user made outside the system."""
    if sold.currency is bought.currency:
        raise CurrencyMismatchError("exchange must involve two different currencies")
    if sold.amount <= 0 or bought.amount <= 0:
        raise ValueError("exchange amounts must be positive")
    rate = (
        (bought.amount / sold.amount)
        if sold.currency is Currency.USD
        else (sold.amount / bought.amount)
    )
    res = conn.execute(
        s.exchange_transactions.insert().values(
            account_id=account_id,
            from_currency=sold.currency.value,
            to_currency=bought.currency.value,
            from_amount=sold.amount,
            to_amount=bought.amount,
            rate=rate,
            executed_at=executed_at,
        )
    )
    pk = res.inserted_primary_key
    assert pk is not None
    return int(pk[0])


def save_snapshot(
    conn: Connection,
    account_id: int,
    book: Book,
    prices: dict[str, Money],
    as_of: datetime,
) -> None:
    mv = book.market_value(prices)
    conn.execute(
        s.portfolio_snapshots.insert().values(
            account_id=account_id,
            currency=book.currency.value,
            as_of=as_of,
            cash=book.cash.amount,
            market_value=mv.amount,
            cost_basis=book.cost_basis().amount,
            realized_pnl=book.realized_pnl.amount,
            unrealized_pnl=(mv - book.cost_basis()).amount,
            nav=(book.cash + mv).amount,
        )
    )


# --- market data ----------------------------------------------------------


def insert_bars(conn: Connection, bars: Iterable[Any]) -> tuple[int, int]:
    """Insert validated ``Bar`` objects. Returns (inserted, duplicates_skipped)."""
    rows = []
    for b in bars:
        check_market_currency(b.market, b.currency)
        rows.append(
            {
                "market": b.market.value,
                "symbol": b.symbol,
                "timeframe": b.timeframe,
                "bar_time": b.bar_time,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "currency": b.currency.value,
                "source": b.source,
                "available_at": b.available_at,
                "ingested_at": b.ingested_at,
            }
        )
    inserted = insert_ignore_duplicates(conn, s.market_data, rows)
    return inserted, len(rows) - inserted


def latest_bar_times(
    conn: Connection, market: Market, timeframe: str = "1d"
) -> dict[str, datetime]:
    m = s.market_data
    rows = conn.execute(
        select(m.c.symbol, func.max(m.c.bar_time))
        .where(m.c.market == market.value, m.c.timeframe == timeframe)
        .group_by(m.c.symbol)
    ).all()
    out: dict[str, datetime] = {}
    for sym, raw in rows:
        # func.max keeps the UtcDateTime type, but be defensive about raw strings.
        out[sym] = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
    return out


# --- AI records -----------------------------------------------------------


def record_ai_analysis(conn: Connection, record: Mapping[str, Any]) -> int:
    rec = dict(record)
    if rec.get("status") != "valid" and rec.get("result_json") is not None:
        raise ValueError("only valid analyses may store a result")
    if rec.get("model_role") == "comparison" and rec.get("used_for_signal"):
        raise ValueError("comparison model output must never be used for signals")
    if rec.get("error_detail"):
        rec["error_detail"] = mask_text(str(rec["error_detail"]))
    res = conn.execute(s.ai_analysis.insert().values(**rec))
    pk = res.inserted_primary_key
    assert pk is not None
    return int(pk[0])


# --- logs, risk events, state ----------------------------------------------


def log_system(
    conn: Connection,
    *,
    ts: datetime,
    level: str,
    component: str,
    message: str,
    context: Mapping[str, Any] | None = None,
) -> None:
    conn.execute(
        s.system_logs.insert().values(
            ts=ts,
            level=level,
            component=component,
            message=mask_text(message),
            context_json=mask_text(json.dumps(dict(context or {}), default=str)),
        )
    )


def record_risk_event(
    conn: Connection,
    *,
    ts: datetime,
    level: str,
    kind: str,
    detail: str,
    account_id: int | None = None,
    currency: Currency | None = None,
) -> int:
    res = conn.execute(
        s.risk_events.insert().values(
            ts=ts,
            level=level,
            kind=kind,
            detail=mask_text(detail),
            account_id=account_id,
            currency=currency.value if currency else None,
        )
    )
    pk = res.inserted_primary_key
    assert pk is not None
    return int(pk[0])


def set_state(conn: Connection, key: str, value: Mapping[str, Any], now: datetime) -> None:
    st = s.system_state
    payload = json.dumps(dict(value), default=str, sort_keys=True)
    exists = conn.execute(select(st.c.key).where(st.c.key == key)).first()
    if exists:
        conn.execute(st.update().where(st.c.key == key).values(value_json=payload, updated_at=now))
    else:
        conn.execute(st.insert().values(key=key, value_json=payload, updated_at=now))


def get_state(conn: Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        select(s.system_state.c.value_json).where(s.system_state.c.key == key)
    ).first()
    return json.loads(row[0]) if row else None
