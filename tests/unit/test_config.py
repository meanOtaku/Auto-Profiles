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
