"""Current schema (re-exports the latest frozen snapshot)."""

from boututln.database.schema_v1 import (
    accounts,
    ai_analysis,
    cash_balances,
    exchange_transactions,
    executions,
    financial_data,
    holdings,
    job_checkpoints,
    job_runs,
    market_data,
    metadata,
    news,
    orders,
    portfolio_snapshots,
    risk_events,
    strategy_signals,
    system_logs,
    system_state,
)

__all__ = [
    "accounts",
    "ai_analysis",
    "cash_balances",
    "exchange_transactions",
    "executions",
    "financial_data",
    "holdings",
    "job_checkpoints",
    "job_runs",
    "market_data",
    "metadata",
    "news",
    "orders",
    "portfolio_snapshots",
    "risk_events",
    "strategy_signals",
    "system_logs",
    "system_state",
    "SCHEMA_HEAD",
]

SCHEMA_HEAD = "0001"
