"""Shared enumerations for orders, run modes and statuses."""

from enum import StrEnum


class TradingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"  # defined only so it can be detected and rejected; never runnable in P0


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMIT = "limit"
    PROTECTED_LIMIT = "protected_limit"  # marketable limit with a price collar


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    FAILED_NOT_SENT = "FAILED_NOT_SENT"


class HaltLevel(StrEnum):
    NONE = "NONE"
    L1 = "L1"  # block new buys
    L2 = "L2"  # block all orders for a book
    L3 = "L3"  # global halt
