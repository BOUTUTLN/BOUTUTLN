"""Strategy interface (implementations arrive in P1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from boututln.domain.market import Instrument


@dataclass(frozen=True)
class Candidate:
    instrument: Instrument
    score: Decimal
    target_weight: Decimal
    as_of: datetime


class QuantStrategy(Protocol):
    name: str

    def candidates(self, as_of: datetime, limit: int) -> list[Candidate]:
        """Return at most ``limit`` (<= 30) candidates using only data available at ``as_of``."""
        ...
