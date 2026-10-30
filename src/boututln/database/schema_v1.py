"""Frozen schema snapshot for migration 0001.

Do not edit this module after release; add a new migration instead.
Money columns are DecimalText; every money row carries an explicit currency,
and CHECK constraints tie markets to their currency (KR->KRW, US->USD).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from boututln.database.types import DecimalText, UtcDateTime

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING)

CURRENCY_CK = "currency IN ('KRW','USD')"
MARKET_CK = "market IN ('KR','US')"
MARKET_CURRENCY_CK = "(market = 'KR' AND currency = 'KRW') OR (market = 'US' AND currency = 'USD')"


def _ts(name: str, nullable: bool = False) -> Column[object]:
    return Column(name, UtcDateTime(), nullable=nullable)


accounts = Table(
    "accounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("broker", String(16), nullable=False),
    # Alias only. Real account numbers are never stored in the database.
    Column("alias", String(64), nullable=False),
    Column("mode", String(16), nullable=False),
    _ts("created_at"),
    UniqueConstraint("broker", "alias", "mode", name="broker_alias_mode"),
    CheckConstraint("broker IN ('KB','SIM')", name="broker"),
    CheckConstraint("mode IN ('backtest','paper')", name="mode_not_live"),
)

cash_balances = Table(
    "cash_balances",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("amount", DecimalText(), nullable=False),
    _ts("updated_at"),
    UniqueConstraint("account_id", "currency", name="account_currency"),
    CheckConstraint(CURRENCY_CK, name="currency"),
)

holdings = Table(
    "holdings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("market", String(2), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("quantity", DecimalText(), nullable=False),
    Column("cost_basis", DecimalText(), nullable=False),
    Column("realized_pnl", DecimalText(), nullable=False, server_default="0"),
    _ts("updated_at"),
    UniqueConstraint("account_id", "market", "symbol", name="account_market_symbol"),
    CheckConstraint(MARKET_CK, name="market"),
    CheckConstraint(CURRENCY_CK, name="currency"),
    CheckConstraint(MARKET_CURRENCY_CK, name="market_currency"),
)

market_data = Table(
    "market_data",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("market", String(2), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("timeframe", String(8), nullable=False),
    _ts("bar_time"),
    Column("open", DecimalText(), nullable=False),
    Column("high", DecimalText(), nullable=False),
    Column("low", DecimalText(), nullable=False),
    Column("close", DecimalText(), nullable=False),
    Column("volume", DecimalText(), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("source", String(32), nullable=False),
    _ts("available_at"),
    _ts("ingested_at"),
    UniqueConstraint("market", "symbol", "timeframe", "bar_time", "source", name="bar_identity"),
    CheckConstraint(MARKET_CK, name="market"),
    CheckConstraint(MARKET_CURRENCY_CK, name="market_currency"),
    CheckConstraint("timeframe IN ('1d','1m')", name="timeframe"),
)

financial_data = Table(
    "financial_data",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("market", String(2), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("source", String(32), nullable=False),
    Column("report_period", String(16), nullable=False),
    Column("item", String(64), nullable=False),
    Column("value", DecimalText(), nullable=False),
    Column("currency", String(3), nullable=True),
    _ts("filed_at"),  # official disclosure time = available_at
    _ts("ingested_at"),
    UniqueConstraint("source", "symbol", "report_period", "item", "filed_at", name="fin_identity"),
    CheckConstraint(MARKET_CK, name="market"),
    CheckConstraint("currency IS NULL OR " + CURRENCY_CK, name="currency"),
)

news = Table(
    "news",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String(32), nullable=False),
    Column("url", Text, nullable=False),
    Column("url_hash", String(64), nullable=False, unique=True),
    Column("title", Text, nullable=False),
    Column("content_hash", String(64), nullable=True),
    Column("symbols_json", Text, nullable=False, server_default="[]"),
    _ts("published_at", nullable=True),
    _ts("first_seen_at"),
)

ai_analysis = Table(
    "ai_analysis",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("market", String(2), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("model_role", String(16), nullable=False),
    Column("model_tag", String(64), nullable=False),
    Column("model_digest", String(80), nullable=True),
    Column("quantization", String(16), nullable=True),
    Column("ollama_version", String(32), nullable=True),
    Column("options_json", Text, nullable=False),
    Column("think", String(8), nullable=False),
    Column("prompt_version", String(32), nullable=False),
    Column("prompt_hash", String(64), nullable=False),
    Column("schema_version", String(8), nullable=False),
    _ts("data_as_of"),
    Column("data_snapshot_id", String(64), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("input_reduced", Boolean, nullable=False, server_default=text("0")),
    _ts("requested_at"),
    _ts("responded_at", nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("input_tokens", Integer, nullable=True),
    Column("output_tokens", Integer, nullable=True),
    Column("attempt", Integer, nullable=False, server_default="1"),
    Column("status", String(16), nullable=False),
    Column("result_json", Text, nullable=True),
    Column("error_kind", String(32), nullable=True),
    Column("error_detail", Text, nullable=True),
    Column("used_for_signal", Boolean, nullable=False, server_default=text("0")),
    CheckConstraint(MARKET_CK, name="market"),
    CheckConstraint("model_role IN ('primary','comparison','fallback')", name="role"),
    CheckConstraint("status IN ('valid','discarded','failed')", name="status"),
    CheckConstraint("status = 'valid' OR result_json IS NULL", name="no_result_unless_valid"),
    CheckConstraint(
        "model_role <> 'comparison' OR used_for_signal = 0", name="comparison_not_used"
    ),
    CheckConstraint("used_for_signal = 0 OR status = 'valid'", name="signal_needs_valid"),
)
Index("ix_ai_analysis_run_symbol", ai_analysis.c.run_id, ai_analysis.c.symbol)

strategy_signals = Table(
    "strategy_signals",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("market", String(2), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("strategy", String(32), nullable=False),
    Column("quant_score", DecimalText(), nullable=False),
    Column("quant_weight", DecimalText(), nullable=False),
    Column("ai_analysis_id", Integer, ForeignKey("ai_analysis.id"), nullable=True),
    Column("final_weight", DecimalText(), nullable=False),
    _ts("as_of"),
    UniqueConstraint(
        "run_id", "account_id", "market", "symbol", "strategy", name="signal_identity"
    ),
    CheckConstraint(MARKET_CK, name="market"),
)

orders = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("intent_id", String(96), nullable=False, unique=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    # Structural lock: the v1 schema cannot store live orders at all.
    Column("mode", String(16), nullable=False),
    Column("market", String(2), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("side", String(4), nullable=False),
    Column("order_type", String(24), nullable=False),
    Column("quantity", DecimalText(), nullable=False),
    Column("limit_price", DecimalText(), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String(24), nullable=False),
    Column("broker_order_no", String(32), nullable=True),
    _ts("created_at"),
    _ts("updated_at"),
    CheckConstraint("mode IN ('backtest','paper')", name="mode_not_live"),
    CheckConstraint(MARKET_CK, name="market"),
    CheckConstraint(MARKET_CURRENCY_CK, name="market_currency"),
    CheckConstraint("side IN ('buy','sell')", name="side"),
    CheckConstraint(
        "status IN ('DRAFT','APPROVED','SUBMITTING','ACKNOWLEDGED','PARTIALLY_FILLED','FILLED',"
        "'CANCEL_PENDING','CANCELLED','REJECTED','EXPIRED','UNKNOWN','FAILED_NOT_SENT')",
        name="status",
    ),
)

executions = Table(
    "executions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("orders.id"), nullable=False),
    Column("quantity", DecimalText(), nullable=False),
    Column("price", DecimalText(), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("fees", DecimalText(), nullable=False),
    Column("taxes", DecimalText(), nullable=False),
    Column("fill_mode", String(16), nullable=False),
    _ts("executed_at"),
    CheckConstraint(CURRENCY_CK, name="currency"),
    CheckConstraint("fill_mode IN ('realtime_sim','posthoc_sim','backtest')", name="fill_mode"),
)

portfolio_snapshots = Table(
    "portfolio_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("currency", String(3), nullable=False),
    _ts("as_of"),
    Column("cash", DecimalText(), nullable=False),
    Column("market_value", DecimalText(), nullable=False),
    Column("cost_basis", DecimalText(), nullable=False),
    Column("realized_pnl", DecimalText(), nullable=False),
    Column("unrealized_pnl", DecimalText(), nullable=False),
    Column("nav", DecimalText(), nullable=False),
    UniqueConstraint("account_id", "currency", "as_of", name="snapshot_identity"),
    CheckConstraint(CURRENCY_CK, name="currency"),
)

exchange_transactions = Table(
    "exchange_transactions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=False),
    Column("from_currency", String(3), nullable=False),
    Column("to_currency", String(3), nullable=False),
    Column("from_amount", DecimalText(), nullable=False),
    Column("to_amount", DecimalText(), nullable=False),
    Column("rate", DecimalText(), nullable=False),
    # The system never converts currency itself; only user-made exchanges are recorded.
    Column("source", String(24), nullable=False, server_default="external_manual"),
    _ts("executed_at"),
    CheckConstraint("from_currency IN ('KRW','USD')", name="from_currency"),
    CheckConstraint("to_currency IN ('KRW','USD')", name="to_currency"),
    CheckConstraint("from_currency <> to_currency", name="different_currencies"),
    CheckConstraint("source = 'external_manual'", name="manual_only"),
)

system_logs = Table(
    "system_logs",
    metadata,
    Column("id", Integer, primary_key=True),
    _ts("ts"),
    Column("level", String(8), nullable=False),
    Column("component", String(64), nullable=False),
    Column("message", Text, nullable=False),  # always masked before insert
    Column("context_json", Text, nullable=False, server_default="{}"),
)

risk_events = Table(
    "risk_events",
    metadata,
    Column("id", Integer, primary_key=True),
    _ts("ts"),
    Column("account_id", Integer, ForeignKey("accounts.id"), nullable=True),
    Column("currency", String(3), nullable=True),
    Column("level", String(8), nullable=False),
    Column("kind", String(48), nullable=False),
    Column("detail", Text, nullable=False),
    _ts("resolved_at", nullable=True),
    CheckConstraint("level IN ('INFO','L1','L2','L3')", name="level"),
    CheckConstraint("currency IS NULL OR " + CURRENCY_CK, name="currency"),
)

system_state = Table(
    "system_state",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value_json", Text, nullable=False),
    _ts("updated_at"),
)

job_runs = Table(
    "job_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job_name", String(48), nullable=False),
    Column("run_date", String(10), nullable=False),
    Column("data_snapshot_id", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    _ts("started_at"),
    _ts("finished_at", nullable=True),
    Column("detail", Text, nullable=True),
    CheckConstraint(
        "status IN ('running','completed','failed','discarded','skipped')", name="status"
    ),
)
# At most one active-or-completed run per job and date (duplicate run prevention).
Index(
    "uq_job_runs_active",
    job_runs.c.job_name,
    job_runs.c.run_date,
    unique=True,
    sqlite_where=text("status IN ('running','completed','failed','skipped')"),
    postgresql_where=text("status IN ('running','completed','failed','skipped')"),
)

job_checkpoints = Table(
    "job_checkpoints",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job_run_id", Integer, ForeignKey("job_runs.id"), nullable=False),
    Column("step", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("payload_json", Text, nullable=False, server_default="{}"),
    _ts("updated_at"),
    UniqueConstraint("job_run_id", "step", name="run_step"),
    CheckConstraint("status IN ('completed','failed')", name="status"),
)
