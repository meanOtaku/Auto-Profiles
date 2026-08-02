"""Load and apply the active profile's stored settings — the missing bridge
between M10 (active-user selection) and M9 (the settings adapter).

HERMES.md's pipeline runs "Select active user" directly into "Load and
apply active profile settings" before "Emit events and persist state".
Neither ``presence/active_user.py`` (selection) nor
``settings/last_used_service.py`` (M11's save-only "read-observe-save"
cycle, see its module docstring) ever reads ``ProfileDatabase.settings``
and applies it through the adapter; this module is the only place that
turns a stable activation into a real settings load+apply.

A profile's settings are applied at most once per activation: repeated
frames for the same still-active profile must never re-touch hardware
(``RateLimitedSettingsAdapter`` also protects against a fast reactivation,
but this module additionally never even attempts a redundant apply). A
failed or rate-limited apply is recorded as a distinct, privacy-safe
``SettingsApplyFailed`` event — never as ``SettingsApplied`` — and is not
retried until the active profile changes again, so a persistently failing
adapter cannot be hammered every frame either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from face_profile.database.repository import ProfileDatabase
from face_profile.presence.active_user import ActiveUserDecision, ActiveUserState
from face_profile.settings import DeviceSettings
from face_profile.settings.rate_limit import RateLimitedSettingsAdapter

logger = logging.getLogger("face_profile.settings.active_profile_applier")


@dataclass(frozen=True, slots=True)
class SettingsApplyOutcome:
    """What happened, if anything, for one active-user update.

    ``attempted`` is true only when an adapter apply call was actually
    made (i.e. stored settings existed for a newly activated profile);
    ``applied`` is true only when that call reported success.
    """

    attempted: bool
    applied: bool
    profile_id: UUID | None
    reason: str


class ActiveProfileSettingsApplier:
    """Apply a newly active profile's stored settings exactly once."""

    def __init__(
        self, *, adapter: RateLimitedSettingsAdapter, database: ProfileDatabase
    ) -> None:
        self._adapter = adapter
        self._database = database
        self._last_seen_profile_id: UUID | None = None

    def sync(self, *, active: ActiveUserDecision) -> SettingsApplyOutcome:
        """React to one M10 active-user update, applying stored settings once."""

        if active.state is not ActiveUserState.ACTIVE or active.profile_id is None:
            self._last_seen_profile_id = None
            return SettingsApplyOutcome(
                attempted=False, applied=False, profile_id=None, reason="no_active_profile"
            )

        if active.profile_id == self._last_seen_profile_id:
            return SettingsApplyOutcome(
                attempted=False,
                applied=False,
                profile_id=active.profile_id,
                reason="already_applied",
            )

        # Mark this activation as handled before attempting the apply so a
        # failure is audited once and never retried every subsequent frame.
        self._last_seen_profile_id = active.profile_id

        stored = self._database.settings.get(active.profile_id)
        if stored is None:
            return SettingsApplyOutcome(
                attempted=False,
                applied=False,
                profile_id=active.profile_id,
                reason="no_stored_settings",
            )

        result = self._adapter.apply(stored, correlation_id=str(active.profile_id))
        if result.applied:
            self._record_event(
                "SettingsApplied", profile_id=active.profile_id, settings=stored, errors=()
            )
            return SettingsApplyOutcome(
                attempted=True, applied=True, profile_id=active.profile_id, reason="applied"
            )

        logger.warning(
            "active profile settings apply failed",
            extra={
                "event_type": "SettingsApplyFailed",
                "profile_id": str(active.profile_id),
                "error_code": ",".join(result.errors) or "unknown",
            },
        )
        self._record_event(
            "SettingsApplyFailed",
            profile_id=active.profile_id,
            settings=stored,
            errors=result.errors,
        )
        return SettingsApplyOutcome(
            attempted=True, applied=False, profile_id=active.profile_id, reason="apply_failed"
        )

    def _record_event(
        self,
        event_type: str,
        *,
        profile_id: UUID,
        settings: DeviceSettings,
        errors: tuple[str, ...],
    ) -> None:
        try:
            self._database.events.record(
                event_type=event_type,
                profile_id=profile_id,
                metadata={
                    "volume": settings.volume,
                    "brightness": settings.brightness,
                    "errors": list(errors),
                },
            )
        except Exception:
            self._database.rollback()
            raise
        self._database.commit()
