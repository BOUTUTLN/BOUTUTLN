"""Paper order gateway: records simulated orders in the local paper database.

It never imports or calls any broker client. The fill engine (order book
simulation, PLAN 8) is P3 scope; in P0 orders stop at ``ACKNOWLEDGED``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from boututln.brokers.gateway import OrderIntent
from boututln.core.errors import BoututlnError, LiveTradingLockedError
from boututln.database import schema as s
from boututln.domain.enums import OrderStatus, TradingMode
from boututln.risk.kill_switch import check_order_allowed


class DuplicateOrderIntentError(BoututlnError):
    pass


class PaperOrderGateway:
    def __init__(self, mode: TradingMode = TradingMode.PAPER) -> None:
        if mode is TradingMode.LIVE:
            raise LiveTradingLockedError("paper gateway cannot run in live mode")
        self.mode: TradingMode = mode

    def submit(self, conn: Connection, intent: OrderIntent, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        check_order_allowed(conn, intent.limit_price.currency, intent.side)
        try:
            with conn.begin_nested():
                res = conn.execute(
                    s.orders.insert().values(
                        intent_id=intent.intent_id,
                        account_id=intent.account_id,
                        mode=self.mode.value,
                        market=intent.market.value,
                        symbol=intent.symbol,
                        side=intent.side.value,
                        order_type=intent.order_type.value,
                        quantity=intent.quantity,
                        limit_price=intent.limit_price.amount,
                        currency=intent.limit_price.currency.value,
                        status=OrderStatus.ACKNOWLEDGED.value,
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as exc:
            if "intent_id" in str(exc.orig) or "UNIQUE" in str(exc.orig).upper():
                raise DuplicateOrderIntentError(
                    f"intent already exists: {intent.intent_id}"
                ) from exc
            raise
        pk = res.inserted_primary_key
        assert pk is not None
        return int(pk[0])
