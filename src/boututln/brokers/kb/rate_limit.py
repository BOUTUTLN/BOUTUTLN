"""Token bucket rate limiter (KB has not published its limits; defaults are conservative)."""

from __future__ import annotations

import time
from collections.abc import Callable

from boututln.brokers.kb.envelope import KBRateLimitedError
from boututln.core.clock import Clock, SystemClock


class TokenBucket:
    def __init__(
        self,
        rate_per_second: int,
        capacity: int | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_wait_seconds: float = 10.0,
    ) -> None:
        if rate_per_second < 1:
            raise ValueError("rate must be >= 1")
        self.rate = rate_per_second
        self.capacity = capacity or rate_per_second
        self._clock = clock or SystemClock()
        self._sleep = sleeper
        self._max_wait = max_wait_seconds
        self._tokens = float(self.capacity)  # token count, not money
        self._last = self._clock.now()

    def _refill(self) -> None:
        now = self._clock.now()
        elapsed = (now - self._last).total_seconds()
        if elapsed > 0:
            self._tokens = min(float(self.capacity), self._tokens + elapsed * self.rate)
            self._last = now

    def try_acquire(self) -> bool:
        self._refill()
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False

    def acquire(self) -> None:
        """Wait (bounded) until a token is available, else raise."""
        waited = 0.0
        while not self.try_acquire():
            need = (1 - self._tokens) / self.rate
            if waited + need > self._max_wait:
                raise KBRateLimitedError("local rate limit: wait would exceed max_wait_seconds")
            self._sleep(need)
            waited += need

    acquire_or_raise = acquire
