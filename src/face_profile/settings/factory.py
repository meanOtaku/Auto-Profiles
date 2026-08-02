"""Construct the configured settings adapter, wrapped for rate limiting."""

from __future__ import annotations

from face_profile.config import PreferenceLearningConfig, SettingsConfig
from face_profile.database.repository import ProfileDatabase
from face_profile.settings import DeviceSettings, MockSettingsAdapter, SettingsAdapter
from face_profile.settings.active_profile_applier import ActiveProfileSettingsApplier
from face_profile.settings.last_used_service import LastUsedPreferenceService
from face_profile.settings.linux import LinuxSettingsAdapter
from face_profile.settings.preference_learning import PreferenceLearner
from face_profile.settings.rate_limit import RateLimitedSettingsAdapter


def create_settings_adapter(
    config: SettingsConfig, *, initial: DeviceSettings
) -> RateLimitedSettingsAdapter:
    """Build the configured adapter, always rate-limited.

    Unlike other M1-M8 factories, this one never returns ``None``: a
    settings adapter is always available (the in-memory mock), since
    recognition and profile code must never be left without a safe
    settings boundary to depend on. Only the *real* backend selection is
    gated by explicit configuration. The return type is the concrete
    ``RateLimitedSettingsAdapter`` (not just ``SettingsAdapter``) because
    M11's preference learner needs its ``recent_self_applications()``.
    """

    if config.backend == "mock":
        base: SettingsAdapter = MockSettingsAdapter(initial)
    else:
        base = LinuxSettingsAdapter()
    return RateLimitedSettingsAdapter(
        base, min_apply_interval_seconds=config.min_apply_interval_seconds
    )


def create_last_used_preference_service(
    config: PreferenceLearningConfig,
    *,
    adapter: RateLimitedSettingsAdapter,
    database: ProfileDatabase,
) -> LastUsedPreferenceService | None:
    """Build the configured preference-learning service, or None unless enabled."""

    if not config.enabled:
        return None
    learner = PreferenceLearner(
        debounce_seconds=config.debounce_seconds,
        min_active_duration_seconds=config.min_active_duration_seconds,
        self_application_tolerance_seconds=config.self_application_tolerance_seconds,
    )
    return LastUsedPreferenceService(adapter=adapter, learner=learner, database=database)


def create_active_profile_settings_applier(
    *, adapter: RateLimitedSettingsAdapter, database: ProfileDatabase | None
) -> ActiveProfileSettingsApplier | None:
    """Build the settings-restore bridge, or ``None`` without a database.

    Unlike preference *learning*, restoring a recognized profile's stored
    settings has no separate opt-in: HERMES.md's pipeline treats "load and
    apply active profile settings" as a mandatory step whenever a profile
    database and an active-user selection exist, not an optional feature.
    """

    if database is None:
        return None
    return ActiveProfileSettingsApplier(adapter=adapter, database=database)
