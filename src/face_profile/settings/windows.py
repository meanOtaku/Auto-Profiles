"""Windows (pycaw volume + WMI brightness) settings adapter -- M17.

Mirrors ``settings/linux.py``'s structure and safety properties exactly:
all host interaction goes through injectable protocols so automated tests
never touch a real Windows API, ``apply()`` is transactional with
best-effort rollback on partial failure, and ``capabilities()`` is a pure,
non-mutating probe. Only two differences in mechanism, both Windows-native:

- **Volume** goes through ``pycaw`` (a thin ``comtypes`` wrapper over the
  Core Audio ``IAudioEndpointVolume`` COM interface) instead of a
  subprocess text-parsing call like ``amixer`` -- there is no standard
  Windows CLI for the default audio endpoint's scalar volume.
- **Brightness** goes through a bounded PowerShell/CIM bridge querying and
  setting the ``WmiMonitorBrightness``/``WmiMonitorBrightnessMethods``
  WMI classes (``root/wmi`` namespace), reusing
  ``settings/linux.py``'s ``CommandRunner`` boundary. Every invocation
  passes an explicit argument list to ``subprocess.run`` with
  ``shell=False`` (never a shell string), and the only caller-controlled
  value ever interpolated into the PowerShell script text is an ``int``
  DeviceSettings has already range-validated to ``0..100`` -- an integer
  cannot carry PowerShell or shell metacharacters, so this is not
  shell-injectable by construction, not merely "usually safe."

Like sysfs backlight (``settings/linux.py``'s own documented Jetson/HDMI
caveat), ``WmiMonitorBrightness`` generally only exists for an internal
laptop panel driven by the OS's own brightness control -- an external
monitor is expected to report brightness as ``UNSUPPORTED``, which is
expected, non-fatal, explicitly reported behavior, not a bug.

**Static verification only.** No Windows machine, COM audio endpoint, or
WMI brightness provider was available while writing this module -- every
code path here was checked with Ruff/mypy and manual review against
documented pycaw/WMI/PowerShell behavior, not exercised at runtime. See
``docs/reports/M17.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from face_profile.settings import (
    AdapterCapabilities,
    ApplyResult,
    CapabilityStatus,
    DeviceSettings,
)
from face_profile.settings.linux import CommandRunner, SubprocessCommandRunner

_POWERSHELL_TIMEOUT_SECONDS = 5.0
_POWERSHELL_ARGS_PREFIX = ("powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command")

_READ_BRIGHTNESS_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$instance = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness "
    "| Select-Object -First 1; "
    "if ($null -eq $instance) { exit 1 }; "
    "Write-Output $instance.CurrentBrightness"
)

_ACCESS_DENIED_MARKERS = ("access is denied", "0x80070005", "accessdenied", "permissiondenied")

_BRIGHTNESS_VALUE_PATTERN = re.compile(r"^\s*(\d{1,3})\s*$")


def _write_brightness_script(value: int) -> str:
    # `value` is always an already-validated int in 0..100 (DeviceSettings'
    # own __post_init__ enforces this before it ever reaches this
    # function), so interpolating it here cannot introduce PowerShell or
    # shell metacharacters -- see the module docstring.
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$instance = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods "
        "| Select-Object -First 1; "
        "if ($null -eq $instance) { exit 1 }; "
        "Invoke-CimMethod -InputObject $instance -MethodName WmiSetBrightness "
        f"-Arguments @{{Timeout=[uint32]0; Brightness=[byte]{int(value)}}} | Out-Null"
    )


def _classify_failure(stderr: str) -> CapabilityStatus:
    lowered = stderr.lower()
    if any(marker in lowered for marker in _ACCESS_DENIED_MARKERS):
        return CapabilityStatus.PERMISSION_DENIED
    return CapabilityStatus.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class _BrightnessProbe:
    status: CapabilityStatus
    value: int | None


class AudioEndpointVolumeAccessor(Protocol):
    """Narrow pycaw boundary so tests never touch a real COM audio endpoint."""

    def read_volume_percent(self) -> int | None: ...

    def write_volume_percent(self, value: int) -> bool: ...

    def capability(self) -> CapabilityStatus: ...


class PycawEndpointVolumeAccessor:
    """Real accessor for the default audio-render endpoint's scalar volume.

    A fresh COM interface pointer is fetched on every call rather than
    cached across calls: ``comtypes`` ties COM apartment initialization to
    the calling thread, and this adapter offers no guarantee about which
    thread invokes it (the API worker thread and CLI/preflight callers may
    differ), so caching a pointer obtained on one thread and using it from
    another would be unsafe. This trades a small amount of per-call COM
    overhead for that safety.
    """

    def _endpoint_volume(self) -> object:
        # Imported lazily: pycaw/comtypes are Windows-only optional
        # dependencies (pyproject.toml's sys_platform == 'win32' marker),
        # never installed on Linux/macOS, so this module must stay
        # importable there -- only actually calling into this accessor
        # requires them to be present.
        from pycaw.pycaw import AudioUtilities  # type: ignore[import-not-found]

        speakers = AudioUtilities.GetSpeakers()
        return speakers.EndpointVolume

    def read_volume_percent(self) -> int | None:
        # comtypes/pycaw raise their own COM-specific exception types (not
        # a narrow, importable-on-Linux hierarchy this module can name at
        # module scope) for anything from "no default device" to a dropped
        # audio session; any failure here degrades to "unknown," exactly
        # like LinuxSettingsAdapter._read_volume_percent()'s non-zero-exit
        # case, rather than crashing the caller.
        try:
            endpoint = self._endpoint_volume()
            scalar = endpoint.GetMasterVolumeLevelScalar()  # type: ignore[attr-defined]
        except Exception:
            return None
        value = round(scalar * 100)
        return value if 0 <= value <= 100 else None

    def write_volume_percent(self, value: int) -> bool:
        try:
            endpoint = self._endpoint_volume()
            endpoint.SetMasterVolumeLevelScalar(value / 100.0, None)  # type: ignore[attr-defined]
        except Exception:
            return False
        return True

    def capability(self) -> CapabilityStatus:
        try:
            self._endpoint_volume()
        except ImportError:
            return CapabilityStatus.UNSUPPORTED
        except Exception as error:
            # Privacy/detail-safe: only a coarse substring check, never the
            # raw COM exception (which can carry host-specific text) is
            # logged or returned to a caller.
            message = str(error).lower()
            if "access" in message and "denied" in message:
                return CapabilityStatus.PERMISSION_DENIED
            return CapabilityStatus.UNSUPPORTED
        return CapabilityStatus.AVAILABLE


class WmiBrightnessAccessor(Protocol):
    """Narrow WMI/PowerShell boundary so tests never invoke a real process."""

    def probe(self) -> _BrightnessProbe: ...

    def write_brightness_percent(self, value: int) -> bool: ...


class PowerShellWmiBrightnessAccessor:
    """Real accessor for internal-display brightness via WMI CIM classes."""

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self._commands = command_runner or SubprocessCommandRunner(
            timeout_seconds=_POWERSHELL_TIMEOUT_SECONDS
        )

    def probe(self) -> _BrightnessProbe:
        result = self._commands.run((*_POWERSHELL_ARGS_PREFIX, _READ_BRIGHTNESS_SCRIPT))
        if result.return_code != 0:
            return _BrightnessProbe(status=_classify_failure(result.stderr), value=None)
        match = _BRIGHTNESS_VALUE_PATTERN.match(result.stdout)
        if match is None:
            return _BrightnessProbe(status=CapabilityStatus.UNSUPPORTED, value=None)
        value = int(match.group(1))
        if not 0 <= value <= 100:
            return _BrightnessProbe(status=CapabilityStatus.UNSUPPORTED, value=None)
        return _BrightnessProbe(status=CapabilityStatus.AVAILABLE, value=value)

    def write_brightness_percent(self, value: int) -> bool:
        result = self._commands.run((*_POWERSHELL_ARGS_PREFIX, _write_brightness_script(value)))
        return result.return_code == 0


class WindowsSettingsAdapter:
    """Apply/read volume via pycaw and brightness via the WMI/PowerShell bridge.

    Semantics mirror ``LinuxSettingsAdapter.apply()`` exactly: each setting
    is applied independently, and if one succeeds while the other fails, a
    best-effort rollback restores the value that already changed, with
    both the original and any rollback failure reported in
    ``ApplyResult.errors``/``rolled_back`` -- never a silent partial
    success. Unsupported/permission-denied settings are rejected before
    any mutation is attempted, exactly like the Linux adapter, and never
    reported as a silent success.
    """

    def __init__(
        self,
        *,
        volume: AudioEndpointVolumeAccessor | None = None,
        brightness: WmiBrightnessAccessor | None = None,
    ) -> None:
        self._volume = volume or PycawEndpointVolumeAccessor()
        self._brightness = brightness or PowerShellWmiBrightnessAccessor()

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            volume=self._volume.capability(), brightness=self._brightness.probe().status
        )

    def read_current(self) -> DeviceSettings:
        volume = self._volume.read_volume_percent()
        brightness = self._brightness.probe().value
        return DeviceSettings(
            volume=volume if volume is not None else 0,
            brightness=brightness if brightness is not None else 0,
        )

    def validate(self, settings: DeviceSettings) -> None:
        DeviceSettings(volume=settings.volume, brightness=settings.brightness)

    def apply(self, settings: DeviceSettings) -> ApplyResult:
        self.validate(settings)
        capabilities = self.capabilities()
        capability_errors = tuple(
            f"{name}_{status.value}"
            for name, status in (
                ("volume", capabilities.volume),
                ("brightness", capabilities.brightness),
            )
            if status != CapabilityStatus.AVAILABLE
        )
        if capability_errors:
            # A full profile contains both values. Refuse before mutation if
            # either capability cannot be applied; never create a transient
            # or lasting half-profile on the host.
            return ApplyResult(applied=False, errors=capability_errors, rolled_back=False)

        previous_volume = self._volume.read_volume_percent()
        previous_brightness = self._brightness.probe().value
        if previous_volume is None and previous_brightness is None:
            return ApplyResult(
                applied=False,
                errors=("volume_read_failed", "brightness_read_failed"),
                rolled_back=False,
            )
        if previous_volume is None:
            return ApplyResult(applied=False, errors=("volume_read_failed",), rolled_back=False)
        if previous_brightness is None:
            return ApplyResult(applied=False, errors=("brightness_read_failed",), rolled_back=False)

        if not self._volume.write_volume_percent(settings.volume):
            return ApplyResult(applied=False, errors=("volume_apply_failed",), rolled_back=False)
        if self._brightness.write_brightness_percent(settings.brightness):
            return ApplyResult(applied=True)

        errors = ["brightness_apply_failed"]
        rolled_back = self._volume.write_volume_percent(previous_volume)
        if not rolled_back:
            errors.append("volume_rollback_failed")
        return ApplyResult(applied=False, errors=tuple(errors), rolled_back=rolled_back)
