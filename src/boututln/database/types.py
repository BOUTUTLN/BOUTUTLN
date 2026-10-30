"""Column types that keep money exact across SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from boututln.domain.money import to_decimal


class DecimalText(TypeDecorator[Decimal]):
    """Stores Decimal as canonical text. Floats are rejected at bind time."""

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        d = to_decimal(value)
        return format(d, "f")

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetimes stored as ISO-8601 UTC text. Naive values are rejected."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError("expected datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime is not allowed; use timezone-aware UTC")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(str(value)).astimezone(UTC)
