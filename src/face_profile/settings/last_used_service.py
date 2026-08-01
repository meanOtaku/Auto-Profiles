"""Ties preference learning to persistence: the M11 read-observe-save cycle.

This is the only M11 component that writes to the M6 database. It reads
the current device settings, asks :class:`PreferenceLearner` whether they
should be saved, and if so persists them to the attributed profile and
records a durable settings-history event in one committed transaction.
"""

from __future__ import annotations

from datetime import datetime

from face_profile.database.repository import ProfileDatabase
from face_profile.presence.active_user import ActiveUserDecision
from face_profile.settings.preference_learning import PreferenceLearner, PreferenceLearnResult
from face_profile.settings.rate_limit import RateLimitedSettingsAdapter


class LastUsedPreferenceService:
    """Observe current settings and persist stable, attributable user changes."""

    def __init__(
        self,
        *,
        adapter: RateLimitedSettingsAdapter,
        learner: PreferenceLearner,
        database: ProfileDatabase,
    ) -> None:
        self._adapter = adapter
        self._learner = learner
        self._database = database

    def observe(self, *, active: ActiveUserDecision, now: datetime) -> PreferenceLearnResult:
        """Read the adapter's current settings and persist them if warranted."""

        current_settings = self._adapter.read_current()
        result = self._learner.observe(
            current_settings=current_settings,
            active=active,
            recent_self_applications=self._adapter.recent_self_applications(),
            now=now,
        )
        if result.saved and result.profile_id is not None and result.settings is not None:
            try:
                self._database.settings.upsert(result.profile_id, result.settings)
                self._database.events.record(
                    event_type="SettingsChanged",
                    profile_id=result.profile_id,
                    metadata={
                        "volume": result.settings.volume,
                        "brightness": result.settings.brightness,
                    },
                )
            except Exception:
                self._database.rollback()
                raise
            self._database.commit()
        return result
