"""RED-first integration test for item 6: a value the service itself applied
on activation must not be learned back as a "user change", while a later,
genuinely different and stable user change must still be persisted to the
active profile. This exercises :class:`ActiveProfileSettingsApplier` (M9/M10
bridge) and :class:`LastUsedPreferenceService` (M11) sharing one
rate-limited adapter and one database, proving the feedback-loop
suppression already implemented in ``PreferenceLearner`` still works once
restore-on-activation is wired in.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from face_profile.database.connection import open_database
from face_profile.database.keys import LocalFileKeyProvider
from face_profile.database.repository import (
    ProfileDatabase,
    ProfileRepository,
    ProfileSettingsRepository,
    RecognitionEventRepository,
)
from face_profile.presence.active_user import ActiveUserDecision, ActiveUserState
from face_profile.settings import AdapterCapabilities, ApplyResult, CapabilityStatus, DeviceSettings
from face_profile.settings.active_profile_applier import ActiveProfileSettingsApplier
from face_profile.settings.last_used_service import LastUsedPreferenceService
from face_profile.settings.preference_learning import PreferenceLearner
from face_profile.settings.rate_limit import RateLimitedSettingsAdapter


class FakeBaseAdapter:
    def __init__(self, *, initial: DeviceSettings) -> None:
        self.current = initial

    def read_current(self) -> DeviceSettings:
        return self.current

    def validate(self, settings: DeviceSettings) -> None:
        DeviceSettings(volume=settings.volume, brightness=settings.brightness)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            volume=CapabilityStatus.AVAILABLE, brightness=CapabilityStatus.AVAILABLE
        )

    def apply(self, settings: DeviceSettings) -> ApplyResult:
        self.current = settings
        return ApplyResult(applied=True)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[ProfileDatabase]:
    connection = open_database(tmp_path / "test.sqlite3")
    key_provider = LocalFileKeyProvider(tmp_path / "test.key")
    db = ProfileDatabase(
        connection=connection,
        profiles=ProfileRepository(connection, key_provider=key_provider),
        settings=ProfileSettingsRepository(connection),
        events=RecognitionEventRepository(connection),
    )
    yield db
    db.close()


def test_self_applied_restore_is_not_learned_but_later_real_change_is(
    database: ProfileDatabase,
) -> None:
    profile = database.profiles.create(display_name="Alice")
    database.settings.upsert(profile.id, DeviceSettings(volume=70, brightness=40))
    database.commit()

    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    clock_time = datetime(2026, 1, 1, tzinfo=UTC)
    adapter = RateLimitedSettingsAdapter(
        base, min_apply_interval_seconds=0.0, clock=lambda: clock_time
    )
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)
    learner = PreferenceLearner(
        debounce_seconds=2.0,
        min_active_duration_seconds=0.0,
        self_application_tolerance_seconds=5.0,
    )
    service = LastUsedPreferenceService(adapter=adapter, learner=learner, database=database)

    decision = ActiveUserDecision(
        state=ActiveUserState.ACTIVE,
        profile_id=profile.id,
        score=1.0,
        active_since=clock_time,
    )

    # Recognition activates Alice: her stored settings are loaded and applied.
    outcome = applier.sync(active=decision)
    assert outcome.applied is True
    assert base.current == DeviceSettings(volume=70, brightness=40)

    # Shortly after, the worker observes the adapter reading back exactly
    # what it just self-applied. This must never be learned as new input.
    clock_time += timedelta(seconds=1)
    result = service.observe(active=decision, now=clock_time)
    assert result.saved is False
    assert result.reason == "self_applied_echo"

    # The real user now manually changes brightness (well past the
    # self-application tolerance window).
    clock_time += timedelta(seconds=10)
    base.current = DeviceSettings(volume=70, brightness=95)

    result = service.observe(active=decision, now=clock_time)
    assert result.saved is False
    assert result.reason == "debouncing"

    # The change is stable for the full debounce window.
    clock_time += timedelta(seconds=3)
    result = service.observe(active=decision, now=clock_time)

    assert result.saved is True
    assert result.profile_id == profile.id
    assert result.settings == DeviceSettings(volume=70, brightness=95)

    stored = database.settings.get(profile.id)
    assert stored == DeviceSettings(volume=70, brightness=95)
