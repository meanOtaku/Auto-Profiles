"""Rate limiting and self-application recording for any settings adapter.

M9 owns the mechanism: recording exactly what the service itself applied
and when, and refusing to hammer real hardware with rapid repeated
changes. The higher-level *policy* of attributing a later observation to
a specific active profile and debouncing it into a saved preference is
M11's job (ARCHITECTURE.md §11); this module only supplies the recorded
evidence M11 will consume.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from face_profile.settings import AdapterCapabilities, ApplyResult, DeviceSettings, SettingsAdapter

Clock = Callable[[], datetime]


class RateLimitError(RuntimeError):
    """Raised when constructed with invalid rate-limit configuration."""


@dataclass(frozen=True, slots=True)
class SelfApplication:
    """One record of a value the service itself applied, for feedback-loop checks."""

    settings: DeviceSettings
    applied_at: datetime
    correlation_id: str | None


class RateLimitedSettingsAdapter:
    """Wrap any :class:`SettingsAdapter` with a minimum apply interval.

    Calls made before ``min_apply_interval_seconds`` has elapsed since the
    last successful apply are rejected with
    ``ApplyResult(applied=False, errors=("rate_limited",))`` rather than
    forwarded to the wrapped adapter, protecting real hardware (audio pop,
    display flicker, driver wear) from rapid repeated writes such as a fast
    slider drag.
    """

    def __init__(
        self,
        adapter: SettingsAdapter,
        *,
        min_apply_interval_seconds: float,
        max_recorded_applications: int = 50,
        clock: Clock | None = None,
    ) -> None:
        if min_apply_interval_seconds < 0.0:
            raise RateLimitError("min_apply_interval_seconds must not be negative")
        if max_recorded_applications < 1:
            raise RateLimitError("max_recorded_applications must be positive")
        self._adapter = adapter
        self._min_interval = min_apply_interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_applied_at: datetime | None = None
        self._recent: deque[SelfApplication] = deque(maxlen=max_recorded_applications)

    def read_current(self) -> DeviceSettings:
        return self._adapter.read_current()

    def validate(self, settings: DeviceSettings) -> None:
        self._adapter.validate(settings)

    def capabilities(self) -> AdapterCapabilities:
        return self._adapter.capabilities()

    def apply(self, settings: DeviceSettings, *, correlation_id: str | None = None) -> ApplyResult:
        """Apply through the wrapped adapter unless the rate limit blocks it."""

        now = self._clock()
        if self._last_applied_at is not None:
            elapsed = (now - self._last_applied_at).total_seconds()
            if elapsed < self._min_interval:
                return ApplyResult(applied=False, errors=("rate_limited",))
        result = self._adapter.apply(settings)
        if result.applied:
            self._last_applied_at = now
            self._recent.append(
                SelfApplication(settings=settings, applied_at=now, correlation_id=correlation_id)
            )
        return result

    def recent_self_applications(self) -> tuple[SelfApplication, ...]:
        """Return bounded, most-recent-last self-applied values for feedback-loop checks."""

        return tuple(self._recent)
