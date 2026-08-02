"""RED-first tests for the missing "load and apply active profile settings"
pipeline step (HERMES.md's High-Level Pipeline, between "Select active user"
and "Emit events and persist state"; M9 provides the adapter, M10 selects
the active user, but nothing previously turned a stable activation into a
settings load+apply).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
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
from face_profile.settings import (
    AdapterCapabilities,
    ApplyResult,
    CapabilityStatus,
    DeviceSettings,
)
from face_profile.settings.active_profile_applier import ActiveProfileSettingsApplier
from face_profile.settings.rate_limit import RateLimitedSettingsAdapter


class FakeBaseAdapter:
    """Controllable fake standing in for the real Linux adapter."""

    def __init__(self, *, initial: DeviceSettings) -> None:
        self.current = initial
        self.apply_calls: list[DeviceSettings] = []
        self.next_result: ApplyResult | None = None

    def read_current(self) -> DeviceSettings:
        return self.current

    def validate(self, settings: DeviceSettings) -> None:
        DeviceSettings(volume=settings.volume, brightness=settings.brightness)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            volume=CapabilityStatus.AVAILABLE, brightness=CapabilityStatus.AVAILABLE
        )

    def apply(self, settings: DeviceSettings) -> ApplyResult:
        self.apply_calls.append(settings)
        if self.next_result is not None:
            result = self.next_result
            self.next_result = None
            if result.applied:
                self.current = settings
            return result
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


def _active(profile_id, *, state: ActiveUserState = ActiveUserState.ACTIVE) -> ActiveUserDecision:
    return ActiveUserDecision(
        state=state,
        profile_id=profile_id,
        score=1.0 if profile_id is not None else None,
        active_since=datetime.now(UTC) if profile_id is not None else None,
    )


def test_stable_transition_applies_stored_settings_exactly_once(
    database: ProfileDatabase,
) -> None:
    profile = database.profiles.create(display_name="Alice")
    database.settings.upsert(profile.id, DeviceSettings(volume=70, brightness=40))
    database.commit()

    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    adapter = RateLimitedSettingsAdapter(base, min_apply_interval_seconds=0.0)
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)

    outcome = applier.sync(active=_active(profile.id))

    assert outcome.applied is True
    assert outcome.profile_id == profile.id
    assert base.apply_calls == [DeviceSettings(volume=70, brightness=40)]


def test_repeated_frames_for_same_active_profile_do_not_reapply(
    database: ProfileDatabase,
) -> None:
    profile = database.profiles.create(display_name="Alice")
    database.settings.upsert(profile.id, DeviceSettings(volume=70, brightness=40))
    database.commit()

    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    adapter = RateLimitedSettingsAdapter(base, min_apply_interval_seconds=0.0)
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)

    decision = _active(profile.id)
    applier.sync(active=decision)
    applier.sync(active=decision)
    applier.sync(active=decision)

    assert len(base.apply_calls) == 1


def test_switching_active_profile_applies_new_profiles_settings(
    database: ProfileDatabase,
) -> None:
    alice = database.profiles.create(display_name="Alice")
    bob = database.profiles.create(display_name="Bob")
    database.settings.upsert(alice.id, DeviceSettings(volume=70, brightness=40))
    database.settings.upsert(bob.id, DeviceSettings(volume=20, brightness=90))
    database.commit()

    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    adapter = RateLimitedSettingsAdapter(base, min_apply_interval_seconds=0.0)
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)

    applier.sync(active=_active(alice.id))
    applier.sync(active=_active(bob.id))

    assert base.apply_calls == [
        DeviceSettings(volume=70, brightness=40),
        DeviceSettings(volume=20, brightness=90),
    ]


def test_profile_with_no_saved_settings_causes_no_mutation(
    database: ProfileDatabase,
) -> None:
    profile = database.profiles.create(display_name="Alice")
    database.commit()

    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    adapter = RateLimitedSettingsAdapter(base, min_apply_interval_seconds=0.0)
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)

    outcome = applier.sync(active=_active(profile.id))

    assert outcome.applied is False
    assert outcome.attempted is False
    assert outcome.reason == "no_stored_settings"
    assert base.apply_calls == []
    assert base.current == DeviceSettings(volume=50, brightness=50)


def test_failed_apply_is_audited_and_not_recorded_as_success(
    database: ProfileDatabase,
) -> None:
    profile = database.profiles.create(display_name="Alice")
    database.settings.upsert(profile.id, DeviceSettings(volume=70, brightness=40))
    database.commit()

    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    base.next_result = ApplyResult(applied=False, errors=("brightness_apply_failed",))
    adapter = RateLimitedSettingsAdapter(base, min_apply_interval_seconds=0.0)
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)

    outcome = applier.sync(active=_active(profile.id))

    assert outcome.applied is False
    assert outcome.attempted is True

    events = database.events.list_recent(limit=10)
    assert any(e.event_type == "SettingsApplyFailed" for e in events)
    assert not any(e.event_type == "SettingsApplied" for e in events)

    # A failure must not turn into hammering: same active profile, next frame,
    # does not retry against real hardware.
    applier.sync(active=_active(profile.id))
    assert len(base.apply_calls) == 1


def test_rate_limited_apply_is_audited_and_not_recorded_as_success(
    database: ProfileDatabase,
) -> None:
    alice = database.profiles.create(display_name="Alice")
    bob = database.profiles.create(display_name="Bob")
    database.settings.upsert(alice.id, DeviceSettings(volume=70, brightness=40))
    database.settings.upsert(bob.id, DeviceSettings(volume=20, brightness=90))
    database.commit()

    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    adapter = RateLimitedSettingsAdapter(base, min_apply_interval_seconds=1000.0)
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)

    first = applier.sync(active=_active(alice.id))
    second = applier.sync(active=_active(bob.id))

    assert first.applied is True
    assert second.applied is False
    # The rate limiter must block before the real hardware call is made.
    assert len(base.apply_calls) == 1

    events = database.events.list_recent(limit=10)
    assert any(
        e.event_type == "SettingsApplyFailed" and e.profile_id == bob.id for e in events
    )
    assert not any(e.event_type == "SettingsApplied" and e.profile_id == bob.id for e in events)


def test_no_active_profile_causes_no_mutation(database: ProfileDatabase) -> None:
    base = FakeBaseAdapter(initial=DeviceSettings(volume=50, brightness=50))
    adapter = RateLimitedSettingsAdapter(base, min_apply_interval_seconds=0.0)
    applier = ActiveProfileSettingsApplier(adapter=adapter, database=database)

    outcome = applier.sync(active=_active(None, state=ActiveUserState.NONE))

    assert outcome.applied is False
    assert outcome.attempted is False
    assert base.apply_calls == []
