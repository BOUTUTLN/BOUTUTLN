"""Kill switch state (PLAN 10). Never liquidates; it only blocks new orders."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection

from boututln.core.errors import BoututlnError
from boututln.database import repositories as repo
from boututln.domain.enums import HaltLevel, Side
from boututln.domain.money import Currency

_ORDER = [HaltLevel.NONE, HaltLevel.L1, HaltLevel.L2, HaltLevel.L3]


class TradingHaltedError(BoututlnError):
    pass


def _key(currency: Currency | None) -> str:
    return "halt:global" if currency is None else f"halt:{currency.value}"


def get_level(conn: Connection, currency: Currency | None = None) -> HaltLevel:
    st = repo.get_state(conn, _key(currency))
    return HaltLevel(st["level"]) if st else HaltLevel.NONE


def set_level(
    conn: Connection,
    level: HaltLevel,
    *,
    reason: str,
    now: datetime,
    currency: Currency | None = None,
) -> None:
    if level is HaltLevel.L3 and currency is not None:
        raise ValueError("L3 is global; do not pass a currency")
    repo.set_state(conn, _key(currency), {"level": level.value, "reason": reason}, now)
    repo.record_risk_event(
        conn,
        ts=now,
        level="INFO" if level is HaltLevel.NONE else level.value,
        kind="halt_set" if level is not HaltLevel.NONE else "halt_cleared",
        detail=reason,
        currency=currency,
    )


def effective_level(conn: Connection, currency: Currency) -> HaltLevel:
    book, glob = get_level(conn, currency), get_level(conn, None)
    return max(book, glob, key=_ORDER.index)


def check_order_allowed(conn: Connection, currency: Currency, side: Side) -> None:
    lvl = effective_level(conn, currency)
    if lvl is HaltLevel.NONE:
        return
    if lvl is HaltLevel.L1 and side is Side.SELL:
        return  # L1 blocks new buys only (protective sells handled by risk rules in P3)
    raise TradingHaltedError(f"{currency.value} book halted at {lvl.value}; {side.value} blocked")
