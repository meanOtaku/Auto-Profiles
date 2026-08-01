"""Last-used preference learning: attribution, debounce, and feedback-loop suppression.

M11 decides whether an observed device-setting change should be saved as
the active profile's new preference, per HERMES.md's attribution rules:
exactly one active profile, active for a minimum duration, the changed
value stable for a debounce period, and not a value the service itself
just applied. This module never applies settings (M9) and never selects
the active user (M10); it only consumes both.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from face_profile.presence.active_user import ActiveUserDecision, ActiveUserState
from face_profile.settings import DeviceSettings
from face_profile.settings.rate_limit import SelfApplication


@dataclass(frozen=True, slots=True)
class PreferenceLearnResult:
    """Whether this observation should be saved as a new stable preference."""

    saved: bool
    profile_id: UUID | None
    settings: DeviceSettings | None
    reason: str


class PreferenceLearner:
    """Stateful attribution/debounce policy for one settings adapter's observations."""

    def __init__(
        self,
        *,
        debounce_seconds: float,
        min_active_duration_seconds: float,
        self_application_tolerance_seconds: float,
    ) -> None:
        if debounce_seconds <= 0.0:
            raise ValueError("debounce_seconds must be positive")
        if min_active_duration_seconds < 0.0:
            raise ValueError("min_active_duration_seconds must not be negative")
        if self_application_tolerance_seconds < 0.0:
            raise ValueError("self_application_tolerance_seconds must not be negative")
        self._debounce_seconds = debounce_seconds
        self._min_active_duration_seconds = min_active_duration_seconds
        self._self_application_tolerance_seconds = self_application_tolerance_seconds

        self._pending_settings: DeviceSettings | None = None
        self._pending_since: datetime | None = None
        self._pending_profile_id: UUID | None = None
        self._last_saved_settings: DeviceSettings | None = None
        self._last_saved_profile_id: UUID | None = None

    def observe(
        self,
        *,
        current_settings: DeviceSettings,
        active: ActiveUserDecision,
        recent_self_applications: Sequence[SelfApplication],
        now: datetime,
    ) -> PreferenceLearnResult:
        """Fold in one observed device-settings reading."""

        if active.state is not ActiveUserState.ACTIVE or active.profile_id is None:
            self._reset_pending()
            return PreferenceLearnResult(
                saved=False, profile_id=None, settings=None, reason="no_active_profile"
            )

        if self._is_self_applied_echo(current_settings, recent_self_applications, now):
            self._reset_pending()
            return PreferenceLearnResult(
                saved=False, profile_id=None, settings=None, reason="self_applied_echo"
            )

        if (
            self._pending_settings != current_settings
            or self._pending_profile_id != active.profile_id
        ):
            self._pending_settings = current_settings
            self._pending_since = now
            self._pending_profile_id = active.profile_id
            return PreferenceLearnResult(
                saved=False, profile_id=None, settings=None, reason="debouncing"
            )

        pending_since = self._pending_since
        if pending_since is None or (now - pending_since).total_seconds() < self._debounce_seconds:
            return PreferenceLearnResult(
                saved=False, profile_id=None, settings=None, reason="debouncing"
            )

        if active.active_since is not None:
            active_duration = (now - active.active_since).total_seconds()
            if active_duration < self._min_active_duration_seconds:
                return PreferenceLearnResult(
                    saved=False, profile_id=None, settings=None, reason="active_duration_too_short"
                )

        if (
            current_settings == self._last_saved_settings
            and active.profile_id == self._last_saved_profile_id
        ):
            return PreferenceLearnResult(
                saved=False, profile_id=None, settings=None, reason="unchanged"
            )

        self._last_saved_settings = current_settings
        self._last_saved_profile_id = active.profile_id
        return PreferenceLearnResult(
            saved=True,
            profile_id=active.profile_id,
            settings=current_settings,
            reason="stable_user_change",
        )

    def _is_self_applied_echo(
        self,
        current_settings: DeviceSettings,
        recent_self_applications: Sequence[SelfApplication],
        now: datetime,
    ) -> bool:
        for application in recent_self_applications:
            if application.settings != current_settings:
                continue
            age = (now - application.applied_at).total_seconds()
            if 0.0 <= age <= self._self_application_tolerance_seconds:
                return True
        return False

    def _reset_pending(self) -> None:
        self._pending_settings = None
        self._pending_since = None
        self._pending_profile_id = None
