"""Provider registry with cost classes, approvals and hard free-tier quotas.

Rules (PLAN 13):
- ``paid`` and ``unknown`` providers are blocked unless listed in the user's
  approval file. KB Open API is ``unknown`` until its fee is confirmed.
- When a free quota is reached, calls stop. There is no fallback to a paid tier.
"""

from __future__ import annotations

import tomllib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from boututln.core.clock import Clock, SystemClock
from boututln.core.config import ProviderConfig
from boututln.core.errors import PaidServiceBlockedError, QuotaExceededError


class CostClass(StrEnum):
    FREE = "free"
    FREE_LIMITED = "free_limited"
    CONDITIONAL_FREE = "conditional_free"
    PAID = "paid"
    UNKNOWN = "unknown"

    @property
    def needs_approval(self) -> bool:
        return self in (CostClass.PAID, CostClass.UNKNOWN)

    @property
    def label_ko(self) -> str:
        return {
            CostClass.FREE: "무료로 이용 가능",
            CostClass.FREE_LIMITED: "무료지만 사용 제한 있음",
            CostClass.CONDITIONAL_FREE: "조건부 무료",
            CostClass.PAID: "유료",
            CostClass.UNKNOWN: "확인 필요",
        }[self]


def load_approvals(path: Path) -> set[str]:
    """Read ``config/approvals/paid_services.toml``; missing file means no approvals."""
    if not path.exists():
        return set()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    approved = set()
    for entry in data.get("approved", []):
        name = entry.get("provider")
        if name and entry.get("approved_by") and entry.get("approved_on"):
            approved.add(str(name))
    return approved


@dataclass
class _Usage:
    day: str = ""
    day_count: int = 0
    recent: deque[datetime] = field(default_factory=deque)


class ProviderRegistry:
    def __init__(
        self,
        providers: list[ProviderConfig],
        approved: set[str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._providers = {p.name: p for p in providers}
        self._approved = set(approved or set())
        self._clock = clock or SystemClock()
        self._usage: dict[str, _Usage] = {}
        self._stopped: dict[str, str] = {}

    def get(self, name: str) -> ProviderConfig:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise PaidServiceBlockedError(f"unregistered provider: {name}") from exc

    def cost_class(self, name: str) -> CostClass:
        return CostClass(self.get(name).cost_class)

    def authorize(self, name: str) -> ProviderConfig:
        p = self.get(name)
        cc = CostClass(p.cost_class)
        if cc.needs_approval and name not in self._approved:
            raise PaidServiceBlockedError(
                f"{name} is '{cc.label_ko}' ({cc.value}); blocked until approved by the user"
            )
        return p

    def acquire(self, name: str) -> None:
        """Authorize and count one call. Raises QuotaExceededError at the limit."""
        p = self.authorize(name)
        now = self._clock.now()
        today = now.date().isoformat()
        if self._stopped.get(name) == today:
            raise QuotaExceededError(f"{name}: free quota reached today; calls stopped")
        u = self._usage.setdefault(name, _Usage())
        if u.day != today:
            u.day, u.day_count = today, 0
            self._stopped.pop(name, None)
        if p.daily_limit is not None and u.day_count >= p.daily_limit:
            self._stopped[name] = today
            raise QuotaExceededError(f"{name}: daily limit {p.daily_limit} reached; calls stopped")
        while u.recent and now - u.recent[0] >= timedelta(minutes=1):
            u.recent.popleft()
        if p.per_minute_limit is not None and len(u.recent) >= p.per_minute_limit:
            raise QuotaExceededError(f"{name}: per-minute limit {p.per_minute_limit} reached")
        if p.per_second_limit is not None:
            last_second = [t for t in u.recent if now - t < timedelta(seconds=1)]
            if len(last_second) >= p.per_second_limit:
                raise QuotaExceededError(f"{name}: per-second limit {p.per_second_limit} reached")
        u.day_count += 1
        u.recent.append(now)

    def usage_today(self, name: str) -> int:
        u = self._usage.get(name)
        if u is None or u.day != self._clock.now().date().isoformat():
            return 0
        return u.day_count

    def non_free(self) -> list[ProviderConfig]:
        """Providers that are not zero-cost (shown separately on the dashboard)."""
        return [
            p
            for p in self._providers.values()
            if CostClass(p.cost_class) in (CostClass.PAID, CostClass.UNKNOWN)
        ]

    def allowed_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for name, p in self._providers.items():
            if CostClass(p.cost_class).needs_approval and name not in self._approved:
                continue
            hosts.update(h.lower() for h in p.hosts)
        return hosts
