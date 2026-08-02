"""RED-first test for item 7 (app wiring): ``create_app`` must build a real
``ActiveProfileSettingsApplier`` and hand it to the ``PipelineWorker`` when
a profile database is configured, and must not fabricate one when the
database is disabled.
"""

from __future__ import annotations

from pathlib import Path

from face_profile.api.app import create_app
from face_profile.config import (
    AppConfig,
    CameraConfig,
    DatabaseConfig,
    DetectionConfig,
    TrackingConfig,
)
from face_profile.settings.active_profile_applier import ActiveProfileSettingsApplier


def test_worker_is_wired_with_a_real_settings_applier_when_database_enabled(
    tmp_path: Path,
) -> None:
    config = AppConfig(
        camera=CameraConfig(enabled=True, source="mock"),
        detection=DetectionConfig(enabled=True),
        tracking=TrackingConfig(enabled=True),
        database=DatabaseConfig(
            enabled=True,
            path=tmp_path / "db.sqlite3",
            key_path=tmp_path / "db.key",
        ),
    )

    app = create_app(config)

    worker = app.state.worker
    assert worker is not None
    assert isinstance(worker._settings_applier, ActiveProfileSettingsApplier)

    app.state.database.close()


def test_worker_has_no_settings_applier_when_database_disabled(tmp_path: Path) -> None:
    config = AppConfig(
        camera=CameraConfig(enabled=True, source="mock"),
        detection=DetectionConfig(enabled=True),
        tracking=TrackingConfig(enabled=True),
        database=DatabaseConfig(enabled=False),
    )

    app = create_app(config)

    worker = app.state.worker
    assert worker is not None
    assert worker._settings_applier is None
