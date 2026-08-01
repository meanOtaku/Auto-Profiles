"""Linux (ALSA + sysfs backlight) settings adapter — the M9 selected platform.

HERMES.md requires selecting one operating system and desktop environment
before implementing a real adapter. This project's development and
deployment baseline is Linux (see docs/reports/M2.md's environment record
and ARCHITECTURE.md §14), and the adapter targets it at the system level
via ALSA's ``amixer`` for volume and the kernel ``/sys/class/backlight``
interface for brightness, rather than a specific desktop-environment
daemon, so it also functions in a headless/container deployment where no
desktop session exists.

All host interaction goes through the injectable :class:`CommandRunner`
and :class:`BacklightAccessor` protocols. Automated tests must supply
fakes; the default, real implementations are only exercised by an
explicit, documented manual test on real hardware, per HERMES.md's M9
acceptance criteria. This module never runs unless explicitly constructed
and selected by configuration (``settings.factory``).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from face_profile.settings import (
    AdapterCapabilities,
    ApplyResult,
    CapabilityStatus,
    DeviceSettings,
)

_VOLUME_PATTERN = re.compile(r"\[(\d{1,3})%\]")
_DEFAULT_MIXER_CONTROL = "Master"
_COMMAND_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of running one external command."""

    return_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Narrow subprocess boundary so tests never invoke a real shell command."""

    def run(self, args: tuple[str, ...]) -> CommandResult: ...


class SubprocessCommandRunner:
    """Real command runner used only by the production Linux adapter."""

    def run(self, args: tuple[str, ...]) -> CommandResult:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return CommandResult(return_code=-1, stdout="", stderr="command execution failed")
        return CommandResult(
            return_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
        )


class BacklightAccessor(Protocol):
    """Narrow sysfs-backlight boundary so tests never touch a real device."""

    def is_available(self) -> bool: ...

    def is_writable(self) -> bool: ...

    def read_brightness_percent(self) -> int | None: ...

    def write_brightness_percent(self, value: int) -> None: ...


class SysfsBacklightAccessor:
    """Real backlight accessor reading/writing the first sysfs backlight device."""

    def __init__(self, base_path: Path = Path("/sys/class/backlight")) -> None:
        self._base_path = base_path

    def _device_path(self) -> Path | None:
        if not self._base_path.is_dir():
            return None
        devices = sorted(p for p in self._base_path.iterdir() if p.is_dir())
        return devices[0] if devices else None

    def is_available(self) -> bool:
        return self._device_path() is not None

    def is_writable(self) -> bool:
        # A non-mutating permission probe; it never writes to the device.
        device = self._device_path()
        if device is None:
            return False
        return os.access(device / "brightness", os.W_OK)

    def _max_brightness(self, device: Path) -> int | None:
        try:
            return int((device / "max_brightness").read_text().strip())
        except (OSError, ValueError):
            return None

    def read_brightness_percent(self) -> int | None:
        device = self._device_path()
        if device is None:
            return None
        maximum = self._max_brightness(device)
        if maximum is None or maximum <= 0:
            return None
        try:
            current = int((device / "brightness").read_text().strip())
        except (OSError, ValueError):
            return None
        return round(current * 100 / maximum)

    def write_brightness_percent(self, value: int) -> None:
        device = self._device_path()
        if device is None:
            raise OSError("no backlight device available")
        maximum = self._max_brightness(device)
        if maximum is None or maximum <= 0:
            raise OSError("backlight device reports no usable maximum brightness")
        raw_value = round(value * maximum / 100)
        (device / "brightness").write_text(str(raw_value))


class LinuxSettingsAdapter:
    """Apply/read volume via ALSA ``amixer`` and brightness via sysfs backlight.

    Partial application (one of the two settings fails after the other
    already succeeded) triggers a best-effort rollback of the value that
    did succeed, and both the original and any rollback failure are
    reported in ``ApplyResult.errors`` rather than silently discarded.
    """

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        backlight: BacklightAccessor | None = None,
        mixer_control: str = _DEFAULT_MIXER_CONTROL,
    ) -> None:
        self._commands = command_runner or SubprocessCommandRunner()
        self._backlight = backlight or SysfsBacklightAccessor()
        self._mixer_control = mixer_control

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            volume=self._volume_capability(), brightness=self._brightness_capability()
        )

    def _volume_capability(self) -> CapabilityStatus:
        # Deliberately routed entirely through the injected CommandRunner
        # (rather than checking shutil.which directly) so capability
        # detection is exercised by fakes in automated tests and behaves
        # identically to every other command-runner call.
        if self._read_volume_percent() is None:
            return CapabilityStatus.UNSUPPORTED
        return CapabilityStatus.AVAILABLE

    def _brightness_capability(self) -> CapabilityStatus:
        if not self._backlight.is_available():
            return CapabilityStatus.UNSUPPORTED
        if self._backlight.read_brightness_percent() is None:
            return CapabilityStatus.UNSUPPORTED
        if not self._backlight.is_writable():
            return CapabilityStatus.PERMISSION_DENIED
        return CapabilityStatus.AVAILABLE

    def read_current(self) -> DeviceSettings:
        volume = self._read_volume_percent()
        brightness = self._backlight.read_brightness_percent()
        return DeviceSettings(
            volume=volume if volume is not None else 0,
            brightness=brightness if brightness is not None else 0,
        )

    def validate(self, settings: DeviceSettings) -> None:
        DeviceSettings(volume=settings.volume, brightness=settings.brightness)

    def apply(self, settings: DeviceSettings) -> ApplyResult:
        self.validate(settings)
        capabilities = self.capabilities()
        errors: list[str] = []

        previous_volume = (
            self._read_volume_percent()
            if capabilities.volume == CapabilityStatus.AVAILABLE
            else None
        )
        volume_applied = False
        if capabilities.volume == CapabilityStatus.AVAILABLE:
            volume_applied = self._write_volume_percent(settings.volume)
            if not volume_applied:
                errors.append("volume_apply_failed")
        else:
            errors.append(f"volume_{capabilities.volume.value}")

        previous_brightness = (
            self._backlight.read_brightness_percent()
            if capabilities.brightness == CapabilityStatus.AVAILABLE
            else None
        )
        brightness_applied = False
        if capabilities.brightness == CapabilityStatus.AVAILABLE:
            try:
                self._backlight.write_brightness_percent(settings.brightness)
                brightness_applied = True
            except OSError:
                errors.append("brightness_apply_failed")
        else:
            errors.append(f"brightness_{capabilities.brightness.value}")

        if volume_applied and brightness_applied:
            return ApplyResult(applied=True)

        rolled_back = True
        if volume_applied and previous_volume is not None:
            rolled_back = self._write_volume_percent(previous_volume) and rolled_back
        if brightness_applied and previous_brightness is not None:
            try:
                self._backlight.write_brightness_percent(previous_brightness)
            except OSError:
                rolled_back = False
        return ApplyResult(applied=False, errors=tuple(errors), rolled_back=rolled_back)

    def _read_volume_percent(self) -> int | None:
        result = self._commands.run(("amixer", "get", self._mixer_control))
        if result.return_code != 0:
            return None
        match = _VOLUME_PATTERN.search(result.stdout)
        if match is None:
            return None
        value = int(match.group(1))
        return value if 0 <= value <= 100 else None

    def _write_volume_percent(self, value: int) -> bool:
        result = self._commands.run(("amixer", "-M", "sset", self._mixer_control, f"{value}%"))
        return result.return_code == 0
