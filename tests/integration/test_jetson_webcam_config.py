"""Safety/validation coverage for the tracked config/jetson-webcam.yaml.

This is the promoted, credential-free generic content of a private Jetson
USB-webcam config: continuous USB webcam capture, integrity-pinned YuNet
detection, and geometric tracking, exposed only through the loopback-only
API from run-continuous.sh. Recognition, enrollment, the profile database,
liveness, the UI, and real host settings must all stay disabled/mock, and
no secret (auth token, non-loopback bind host) may ever be tracked here --
see docs/RUNNING_ON_UBUNTU.md section 5.
"""

import re
from pathlib import Path

from face_profile.config import load_config

_REPO_ROOT = Path(__file__).parents[2]
_JETSON_WEBCAM_CONFIG = _REPO_ROOT / "config" / "jetson-webcam.yaml"


def test_jetson_webcam_config_is_safe_loopback_webcam_detection_tracking_only() -> None:
    assert _JETSON_WEBCAM_CONFIG.is_file(), "config/jetson-webcam.yaml must ship in the repository"
    config = load_config(_JETSON_WEBCAM_CONFIG)

    assert config.camera.enabled is True
    assert config.camera.source == "webcam"
    assert config.camera.path is None

    assert config.detection.enabled is True
    assert config.detection.backend == "yunet"
    assert config.detection.model_path == Path("models/face_detection_yunet_2023mar.onnx")
    assert config.detection.model_sha256 == (
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
    )

    assert config.tracking.enabled is True

    assert config.api.enabled is True
    assert config.api.bind_host == "127.0.0.1"
    assert config.api.auth_token is None

    for name in (
        "quality",
        "embedding",
        "database",
        "recognition",
        "enrollment",
        "active_user",
        "preference_learning",
        "ui",
        "liveness",
    ):
        section = getattr(config, name)
        assert section.enabled is False, f"{name}.enabled must stay disabled"

    assert config.enrollment.automatic_promotion is False
    assert config.embedding.backend == "mock"
    assert config.settings.backend == "mock"


def test_jetson_webcam_config_contains_no_credential_or_biometric_material() -> None:
    text = _JETSON_WEBCAM_CONFIG.read_text(encoding="utf-8")
    assert re.findall(r"^\s*auth_token:.*$", text, flags=re.MULTILINE) == ["  auth_token: null"]
    assert "bind_host: 127.0.0.1" in text
    assert "bind_host: 0.0.0.0" not in text
    assert "Bearer " not in text
