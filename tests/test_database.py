from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from boututln.database import repositories as repo
from boututln.database import schema as s
from boututln.database.engine import create_db_engine
from boututln.database.migrations import current_revision, upgrade_to_head
from boututln.domain.enums import Side, TradingMode
from boututln.domain.market import Instrument, Market
from boututln.domain.money import Currency, CurrencyMismatchError, Money
from boututln.portfolio.ledger import Book, Trade

T = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
REQUIRED_TABLES = {
    "accounts",
    "cash_balances",
    "holdings",
    "market_data",
    "financial_data",
    "news",
    "ai_analysis",
    "strategy_signals",
    "orders",
    "executions",
    "portfolio_snapshots",
    "exchange_transactions",
    "system_logs",
    "risk_events",
}


def _account(engine: Engine) -> int:
    with engine.begin() as c:
        return repo.create_account(c, broker="SIM", alias="paper-1", mode=TradingMode.PAPER, now=T)


def test_init_creates_all_tables_and_is_idempotent(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert REQUIRED_TABLES <= tables
    assert {"job_runs", "job_checkpoints", "system_state"} <= tables
    assert upgrade_to_head(engine) == "0001"  # second run does nothing
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_live_account_and_live_order_rejected_by_schema(engine: Engine) -> None:
    with pytest.raises(ValueError):
        with engine.begin() as c:
            repo.create_account(c, broker="KB", alias="x", mode=TradingMode.LIVE, now=T)
    acc = _account(engine)
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                s.orders.insert().values(
                    intent_id="i1",
                    account_id=acc,
                    mode="live",
                    market="KR",
                    symbol="005930",
                    side="buy",
                    order_type="limit",
                    quantity=Decimal(1),
                    limit_price=Decimal(100),
                    currency="KRW",
                    status="DRAFT",
                    created_at=T,
                    updated_at=T,
                )
            )


def test_market_currency_check_constraints(engine: Engine) -> None:
    acc = _account(engine)
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                s.holdings.insert().values(
                    account_id=acc,
                    market="KR",
                    symbol="005930",
                    currency="USD",
                    quantity=Decimal(1),
                    cost_basis=Decimal(1),
                    updated_at=T,
                )
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                s.cash_balances.insert().values(
                    account_id=acc, currency="JPY", amount=Decimal(1), updated_at=T
                )
            )


def test_float_and_naive_datetime_rejected_at_bind(engine: Engine) -> None:
    acc = _account(engine)
    with pytest.raises(StatementError):
        with engine.begin() as c:
            c.execute(
                s.cash_balances.insert().values(
                    account_id=acc, currency="KRW", amount=0.1, updated_at=T
                )
            )
    with pytest.raises(StatementError):
        with engine.begin() as c:
            c.execute(
                s.cash_balances.insert().values(
                    account_id=acc,
                    currency="KRW",
                    amount=Decimal(1),
                    updated_at=datetime(2026, 1, 1),
                )
            )


def test_books_saved_separately_and_reloaded_exactly(engine: Engine) -> None:
    acc = _account(engine)
    kb, ub = Book(Currency.KRW), Book(Currency.USD)
    kb.deposit(Money.of(1_000_000, "KRW"))
    ub.deposit(Money.of("7000.00", "USD"))
    z_k, z_u = Money.zero(Currency.KRW), Money.zero(Currency.USD)
    kb.apply(
        Trade(
            Instrument(Market.KR, "005930"),
            Side.BUY,
            Decimal(3),
            Money.of(70000, "KRW"),
            Money.of(30, "KRW"),
            z_k,
            T,
        )
    )
    ub.apply(
        Trade(
            Instrument(Market.US, "SPY"),
            Side.BUY,
            Decimal(2),
            Money.of("500.25", "USD"),
            z_u,
            z_u,
            T,
        )
    )
    with engine.begin() as c:
        repo.save_book(c, acc, kb, T)
        repo.save_book(c, acc, ub, T)
    with engine.connect() as c:
        k2, u2 = repo.load_book(c, acc, Currency.KRW), repo.load_book(c, acc, Currency.USD)
    assert k2.cash == Money.of(789_970, "KRW")
    assert u2.cash == Money.of("5999.50", "USD")
    assert set(k2.positions) == {"KR:005930"} and set(u2.positions) == {"US:SPY"}
    assert k2.positions["KR:005930"].cost_basis == Money.of(210_030, "KRW")
    assert u2.positions["US:SPY"].average_cost == Money.of("500.25", "USD")
    with pytest.raises(CurrencyMismatchError):
        with engine.begin() as c:
            repo.set_cash(c, acc, Money.of(1, "USD"), T)
            # saving a KRW book with a USD position must fail
            bad = Book(Currency.KRW)
            from boututln.portfolio.ledger import Position

            bad.positions["US:SPY"] = Position(
                Instrument(Market.US, "SPY"), Decimal(1), Money.of(1, "USD")
            )
            repo.save_book(c, acc, bad, T)


def test_external_exchange_is_recorded_only(engine: Engine) -> None:
    acc = _account(engine)
    with engine.begin() as c:
        repo.record_external_exchange(
            c, acc, sold=Money.of(1_400_000, "KRW"), bought=Money.of(1000, "USD"), executed_at=T
        )
        row = c.execute(select(s.exchange_transactions)).one()
    assert row.rate == Decimal(1400) and row.source == "external_manual"
    with pytest.raises(CurrencyMismatchError):
        with engine.begin() as c:
            repo.record_external_exchange(
                c, acc, sold=Money.of(1, "KRW"), bought=Money.of(1, "KRW"), executed_at=T
            )


def test_restart_keeps_committed_and_drops_uncommitted(tmp_path: Path) -> None:
    path = tmp_path / "db" / "paper.db"
    e1 = create_db_engine(path)
    upgrade_to_head(e1)
    acc = _account(e1)
    with e1.begin() as c:
        repo.set_cash(c, acc, Money.of(500, "KRW"), T)
    conn = e1.connect()
    tx = conn.begin()
    repo.set_cash(conn, acc, Money.of(999, "KRW"), T)
    conn.close()  # process "dies" before commit -> rolled back
    e1.dispose()
    e2 = create_db_engine(path)
    assert current_revision(e2) == "0001"
    with e2.connect() as c:
        assert repo.get_cash(c, acc, Currency.KRW) == Money.of(500, "KRW")
    del tx
    e2.dispose()


def test_ai_analysis_constraints(engine: Engine) -> None:
    base = dict(
        run_id="r",
        market="US",
        symbol="SPY",
        model_role="comparison",
        model_tag="gemma4:12b-it-q4_K_M",
        options_json="{}",
        think="false",
        prompt_version="p1",
        prompt_hash="h",
        schema_version="1",
        data_as_of=T,
        data_snapshot_id="snap",
        input_hash="i",
        requested_at=T,
        status="valid",
        result_json="{}",
    )
    with pytest.raises(ValueError):
        with engine.begin() as c:
            repo.record_ai_analysis(c, dict(base, used_for_signal=True))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(s.ai_analysis.insert().values(**dict(base, used_for_signal=True)))
    with pytest.raises(ValueError):
        with engine.begin() as c:
            repo.record_ai_analysis(c, dict(base, status="discarded"))
    with engine.begin() as c:
        repo.record_ai_analysis(
            c,
            dict(base, status="failed", result_json=None, error_detail="appSecret=abcd1234 leaked"),
        )
        detail = c.execute(select(s.ai_analysis.c.error_detail)).scalar_one()
    assert "abcd1234" not in detail


def test_system_logs_are_masked(engine: Engine) -> None:
    with engine.begin() as c:
        repo.log_system(
            c,
            ts=T,
            level="INFO",
            component="t",
            message="Authorization: Bearer eyJsecretTOKEN",
            context={"appKey": "K1234"},
        )
        row = c.execute(select(s.system_logs)).one()
    assert "eyJsecretTOKEN" not in row.message and "K1234" not in row.context_json


def test_wal_checkpoint_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    e = create_db_engine(path)
    upgrade_to_head(e)
    with e.begin() as c:
        c.execute(text("PRAGMA wal_checkpoint(FULL)"))
    e.dispose()
    assert path.exists()
