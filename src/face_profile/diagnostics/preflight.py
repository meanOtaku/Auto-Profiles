"""Non-mutating deployment preflight: models, camera path, settings capabilities.

Every check here only ever reads: model bytes are hashed but never loaded
into an inference engine, the camera device is only ``stat``-ed and
permission-checked (never opened), and settings capability detection goes
through the same ``LinuxSettingsAdapter.capabilities()`` the real adapter
uses in production -- itself documented as read-only (see
``settings/linux.py``'s ``SysfsBacklightAccessor.is_writable`` docstring
and ``LinuxSettingsAdapter._volume_capability``). Nothing here changes
volume, brightness, or any other host state.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from face_profile.config import AppConfig
from face_profile.settings import AdapterCapabilities, CapabilityStatus
from face_profile.settings.linux import LinuxSettingsAdapter


class CheckStatus(StrEnum):
    """Outcome of one non-mutating preflight check."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One named check's outcome and a human-readable detail string."""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Every check run for one configuration."""

    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        """True unless at least one check explicitly failed."""

        return all(check.status is not CheckStatus.FAIL for check in self.checks)


class CapabilitiesProvider(Protocol):
    """Narrow boundary so tests never construct a real ``LinuxSettingsAdapter``."""

    def capabilities(self) -> AdapterCapabilities: ...


VideoDevicePath = Callable[[int], Path]
LinuxAdapterFactory = Callable[[], CapabilitiesProvider]


def _default_video_device_path(device_index: int) -> Path:
    return Path(f"/dev/video{device_index}")


def run_preflight(
    config: AppConfig,
    *,
    video_device_path: VideoDevicePath = _default_video_device_path,
    linux_adapter_factory: LinuxAdapterFactory = LinuxSettingsAdapter,
) -> PreflightReport:
    """Run every non-mutating check for one loaded, already-validated config."""

    checks = (
        _check_model(
            "detection_model",
            enabled=config.detection.enabled,
            is_mock=config.detection.backend == "mock",
            model_path=config.detection.model_path,
            model_sha256=config.detection.model_sha256,
        ),
        _check_model(
            "embedding_model",
            enabled=config.embedding.enabled,
            is_mock=config.embedding.backend == "mock",
            model_path=config.embedding.model_path,
            model_sha256=config.embedding.model_sha256,
        ),
        _check_camera(config, video_device_path),
        _check_settings(config, linux_adapter_factory),
    )
    return PreflightReport(checks=checks)


def _check_model(
    name: str,
    *,
    enabled: bool,
    is_mock: bool,
    model_path: Path | None,
    model_sha256: str | None,
) -> CheckResult:
    if not enabled or is_mock:
        return CheckResult(name, CheckStatus.SKIPPED, "disabled or mock backend")
    if model_path is None or model_sha256 is None:
        return CheckResult(name, CheckStatus.FAIL, "model path/sha256 not configured")
    try:
        data = model_path.read_bytes()
    except OSError:
        return CheckResult(name, CheckStatus.FAIL, f"model file unavailable: {model_path}")
    actual = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual, model_sha256):
        return CheckResult(
            name, CheckStatus.FAIL, f"sha256 mismatch: expected {model_sha256}, got {actual}"
        )
    return CheckResult(name, CheckStatus.PASS, f"verified {len(data)} bytes at {model_path}")


def _check_camera(config: AppConfig, video_device_path: VideoDevicePath) -> CheckResult:
    name = "camera_device"
    if not config.camera.enabled:
        return CheckResult(name, CheckStatus.SKIPPED, "camera disabled")
    if config.camera.source != "webcam":
        return CheckResult(name, CheckStatus.SKIPPED, f"source={config.camera.source}, not webcam")
    path = video_device_path(config.camera.device_index)
    if not path.exists():
        return CheckResult(name, CheckStatus.FAIL, f"{path} does not exist")
    readable = os.access(path, os.R_OK)
    writable = os.access(path, os.W_OK)
    if not (readable and writable):
        return CheckResult(
            name,
            CheckStatus.FAIL,
            f"{path} exists but is not fully accessible (readable={readable}, writable={writable})",
        )
    return CheckResult(name, CheckStatus.PASS, f"{path} exists and is read/write accessible")


def _check_settings(config: AppConfig, linux_adapter_factory: LinuxAdapterFactory) -> CheckResult:
    name = "settings_capabilities"
    if config.settings.backend != "linux":
        return CheckResult(name, CheckStatus.SKIPPED, "settings.backend is not linux")
    capabilities = linux_adapter_factory().capabilities()
    detail = f"volume={capabilities.volume.value}, brightness={capabilities.brightness.value}"
    if capabilities.volume != CapabilityStatus.AVAILABLE:
        return CheckResult(name, CheckStatus.FAIL, f"amixer volume control not available: {detail}")
    return CheckResult(name, CheckStatus.PASS, detail)
