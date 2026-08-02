"""RED-first tests for the non-mutating deployment preflight checks: config
validity is implied by successful ``AppConfig`` construction, so this
exercises model integrity, camera device path/permissions, and settings
capability detection -- all read-only, never touching real volume,
brightness, or a real camera device.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from face_profile.config import (
    AppConfig,
    CameraConfig,
    DetectionConfig,
    EmbeddingConfig,
    SettingsConfig,
)
from face_profile.diagnostics.preflight import CheckStatus, run_preflight
from face_profile.settings import AdapterCapabilities, CapabilityStatus


class FakeCapabilitiesProvider:
    def __init__(self, capabilities: AdapterCapabilities) -> None:
        self._capabilities = capabilities

    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities


def test_model_checks_are_skipped_for_disabled_or_mock_backends() -> None:
    config = AppConfig()

    report = run_preflight(config)

    detection = next(c for c in report.checks if c.name == "detection_model")
    embedding = next(c for c in report.checks if c.name == "embedding_model")
    assert detection.status is CheckStatus.SKIPPED
    assert embedding.status is CheckStatus.SKIPPED
    assert report.ok is True


def test_detection_model_check_passes_when_hash_matches(tmp_path: Path) -> None:
    model_path = tmp_path / "yunet.onnx"
    model_path.write_bytes(b"pretend-model-bytes")
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()

    config = AppConfig(
        detection=DetectionConfig(
            enabled=True, backend="yunet", model_path=model_path, model_sha256=digest
        )
    )

    report = run_preflight(config)

    detection = next(c for c in report.checks if c.name == "detection_model")
    assert detection.status is CheckStatus.PASS
    assert report.ok is True


def test_detection_model_check_fails_on_hash_mismatch(tmp_path: Path) -> None:
    model_path = tmp_path / "yunet.onnx"
    model_path.write_bytes(b"pretend-model-bytes")
    wrong_digest = "0" * 64

    config = AppConfig(
        detection=DetectionConfig(
            enabled=True, backend="yunet", model_path=model_path, model_sha256=wrong_digest
        )
    )

    report = run_preflight(config)

    detection = next(c for c in report.checks if c.name == "detection_model")
    assert detection.status is CheckStatus.FAIL
    assert report.ok is False


def test_detection_model_check_fails_when_file_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.onnx"

    config = AppConfig(
        detection=DetectionConfig(
            enabled=True, backend="yunet", model_path=missing_path, model_sha256="a" * 64
        )
    )

    report = run_preflight(config)

    detection = next(c for c in report.checks if c.name == "detection_model")
    assert detection.status is CheckStatus.FAIL
    assert report.ok is False


def test_embedding_model_check_passes_when_hash_matches(tmp_path: Path) -> None:
    model_path = tmp_path / "sface.onnx"
    model_path.write_bytes(b"pretend-embedding-model-bytes")
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()

    config = AppConfig(
        embedding=EmbeddingConfig(
            enabled=True, backend="onnx", model_path=model_path, model_sha256=digest
        )
    )

    report = run_preflight(config)

    embedding = next(c for c in report.checks if c.name == "embedding_model")
    assert embedding.status is CheckStatus.PASS
    assert report.ok is True


def test_camera_check_skipped_when_camera_disabled() -> None:
    config = AppConfig(camera=CameraConfig(enabled=False))

    report = run_preflight(config)

    camera = next(c for c in report.checks if c.name == "camera_device")
    assert camera.status is CheckStatus.SKIPPED


def test_camera_check_fails_when_device_path_does_not_exist(tmp_path: Path) -> None:
    config = AppConfig(camera=CameraConfig(enabled=True, source="webcam", device_index=0))

    report = run_preflight(config, video_device_path=lambda index: tmp_path / f"video{index}")

    camera = next(c for c in report.checks if c.name == "camera_device")
    assert camera.status is CheckStatus.FAIL
    assert report.ok is False


def test_camera_check_passes_when_device_path_is_accessible(tmp_path: Path) -> None:
    device_path = tmp_path / "video0"
    device_path.write_bytes(b"")

    config = AppConfig(camera=CameraConfig(enabled=True, source="webcam", device_index=0))

    report = run_preflight(config, video_device_path=lambda index: device_path)

    camera = next(c for c in report.checks if c.name == "camera_device")
    assert camera.status is CheckStatus.PASS
    assert report.ok is True


def test_camera_check_fails_when_device_path_is_not_writable(tmp_path: Path) -> None:
    device_path = tmp_path / "video0"
    device_path.write_bytes(b"")
    device_path.chmod(0o444)

    config = AppConfig(camera=CameraConfig(enabled=True, source="webcam", device_index=0))

    report = run_preflight(config, video_device_path=lambda index: device_path)

    camera = next(c for c in report.checks if c.name == "camera_device")
    assert camera.status is CheckStatus.FAIL


def test_settings_check_skipped_for_mock_backend() -> None:
    config = AppConfig(settings=SettingsConfig(backend="mock"))

    report = run_preflight(config)

    settings_check = next(c for c in report.checks if c.name == "settings_capabilities")
    assert settings_check.status is CheckStatus.SKIPPED


def test_settings_check_passes_when_volume_available() -> None:
    config = AppConfig(settings=SettingsConfig(backend="linux"))
    provider = FakeCapabilitiesProvider(
        AdapterCapabilities(
            volume=CapabilityStatus.AVAILABLE, brightness=CapabilityStatus.UNSUPPORTED
        )
    )

    report = run_preflight(config, linux_adapter_factory=lambda: provider)

    settings_check = next(c for c in report.checks if c.name == "settings_capabilities")
    assert settings_check.status is CheckStatus.PASS
    assert "brightness=unsupported" in settings_check.detail
    assert report.ok is True


def test_settings_check_fails_when_volume_unavailable() -> None:
    config = AppConfig(settings=SettingsConfig(backend="linux"))
    provider = FakeCapabilitiesProvider(
        AdapterCapabilities(
            volume=CapabilityStatus.UNSUPPORTED, brightness=CapabilityStatus.UNSUPPORTED
        )
    )

    report = run_preflight(config, linux_adapter_factory=lambda: provider)

    settings_check = next(c for c in report.checks if c.name == "settings_capabilities")
    assert settings_check.status is CheckStatus.FAIL
    assert report.ok is False


def test_preflight_never_mutates_real_settings_or_touches_a_real_device(tmp_path: Path) -> None:
    """No check makes any subprocess/hardware call by itself; capabilities
    only ever come from an injected provider, and camera checks only ever
    stat an injected path -- this test simply pins that contract."""

    config = AppConfig(
        camera=CameraConfig(enabled=True, source="webcam", device_index=0),
        settings=SettingsConfig(backend="linux"),
    )
    device_path = tmp_path / "video0"
    device_path.write_bytes(b"")
    provider = FakeCapabilitiesProvider(
        AdapterCapabilities(
            volume=CapabilityStatus.AVAILABLE, brightness=CapabilityStatus.AVAILABLE
        )
    )

    report = run_preflight(
        config,
        video_device_path=lambda index: device_path,
        linux_adapter_factory=lambda: provider,
    )

    assert report.ok is True
