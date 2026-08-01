"""Fixed-window per-client rate limiting for the M12 API.

A single-process, in-memory limiter. It is sufficient for the one-process
headless daemon this milestone targets; a multi-process or multi-instance
deployment would need a shared store, a documented limitation.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


class RateLimiter:
    """Sliding-window request-count limiter keyed by an arbitrary client key."""

    def __init__(self, *, limit_per_minute: int, clock: Clock | None = None) -> None:
        if limit_per_minute < 1:
            raise ValueError("limit_per_minute must be positive")
        self._limit = limit_per_minute
        self._clock = clock or (lambda: datetime.now(UTC))
        self._requests: dict[str, deque[datetime]] = {}

    def allow(self, key: str) -> bool:
        """Record one request attempt and return whether it is within limits."""

        now = self._clock()
        window = self._requests.setdefault(key, deque())
        while window and (now - window[0]).total_seconds() >= 60.0:
            window.popleft()
        if len(window) >= self._limit:
            return False
        window.append(now)
        return True
