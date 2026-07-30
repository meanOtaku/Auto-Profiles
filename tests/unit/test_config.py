from pathlib import Path

import pytest


def test_load_config_accepts_hardware_disabled_defaults(tmp_path: Path) -> None:
    from face_profile.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        """
camera:
  enabled: false
logging:
  level: INFO
settings:
  volume: 50
  brightness: 50
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.camera.enabled is False
    assert config.logging.level == "INFO"
    assert config.settings.volume == 50
    assert config.settings.brightness == 50


def test_load_config_reports_missing_file_without_leaking_traceback(tmp_path: Path) -> None:
    from face_profile.config import ConfigurationError, load_config

    missing = tmp_path / "missing.yaml"

    with pytest.raises(ConfigurationError, match="configuration file not found"):
        load_config(missing)


def test_load_config_rejects_unknown_keys_with_controlled_error(tmp_path: Path) -> None:
    from face_profile.config import ConfigurationError, load_config

    path = tmp_path / "config.yaml"
    path.write_text("camera:\n  enabled: false\n  device: secret-camera\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid configuration"):
        load_config(path)


def test_load_config_rejects_duplicate_keys(tmp_path: Path) -> None:
    from face_profile.config import ConfigurationError, load_config

    path = tmp_path / "config.yaml"
    path.write_text("camera:\n  enabled: false\n  enabled: true\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid configuration"):
        load_config(path)


def test_load_config_wraps_unhashable_yaml_keys(tmp_path: Path) -> None:
    from face_profile.config import ConfigurationError, load_config

    path = tmp_path / "config.yaml"
    path.write_text("? [camera, settings]\n: invalid\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="invalid configuration"):
        load_config(path)


def test_load_config_accepts_image_source_configuration(tmp_path: Path) -> None:
    from face_profile.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "camera:\n  enabled: true\n  source: image\n  path: fixture.png\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.camera.source == "image"
    assert config.camera.path == Path("fixture.png")
    assert config.camera.device_index == 0
    assert config.camera.retry_attempts == 2


def test_load_config_requires_path_for_file_sources(tmp_path: Path) -> None:
    from face_profile.config import ConfigurationError, load_config

    path = tmp_path / "config.yaml"
    path.write_text("camera:\n  enabled: true\n  source: video\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid configuration"):
        load_config(path)


def test_load_config_rejects_path_for_non_file_source(tmp_path: Path) -> None:
    from face_profile.config import ConfigurationError, load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "camera:\n  enabled: true\n  source: webcam\n  path: fixture.png\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="invalid configuration"):
        load_config(path)


def test_load_config_rejects_webcam_controls_for_file_source(tmp_path: Path) -> None:
    from face_profile.config import ConfigurationError, load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "camera:\n"
        "  enabled: true\n"
        "  source: image\n"
        "  path: fixture.png\n"
        "  device_index: 999\n"
        "  retry_attempts: 10\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="invalid configuration"):
        load_config(path)


def test_detection_config_requires_model_integrity_for_yunet() -> None:
    from pydantic import ValidationError

    from face_profile.config import DetectionConfig

    with pytest.raises(ValidationError, match="model path and SHA-256"):
        DetectionConfig(enabled=True, backend="yunet")


def test_load_config_accepts_integrity_pinned_yunet_detector(tmp_path: Path) -> None:
    from face_profile.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "detection:\n"
        "  enabled: true\n"
        "  backend: yunet\n"
        "  model_path: detector.onnx\n"
        f"  model_sha256: {'a' * 64}\n"
        "  confidence_threshold: 0.6\n"
        "  nms_threshold: 0.2\n"
        "  top_k: 250\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.detection.model_path == Path("detector.onnx")
    assert config.detection.model_sha256 == "a" * 64
    assert config.detection.confidence_threshold == 0.6
