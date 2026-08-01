"""Portable settings boundary and deterministic test adapter.

Recognition and profile code must never call an OS-specific adapter
directly; only this module's ``SettingsAdapter`` contract is depended on
elsewhere, per HERMES.md's settings-system rule and CODING_STANDARDS.md
§4. M9 adds capability/permission reporting so unsupported operations are
explicit rather than silently ignored.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class SettingsValidationError(ValueError):
    """Raised when a portable setting is outside its safe range."""


@dataclass(frozen=True, slots=True)
class DeviceSettings:
    """Portable device settings represented as percentages."""

    volume: int
    brightness: int

    def __post_init__(self) -> None:
        for name, value in (("volume", self.volume), ("brightness", self.brightness)):
            if type(value) is not int or not 0 <= value <= 100:
                raise SettingsValidationError(f"{name} must be an integer between 0 and 100")


class CapabilityStatus(StrEnum):
    """Whether one setting can currently be read or applied on this host."""

    AVAILABLE = "available"
    UNSUPPORTED = "unsupported"
    PERMISSION_DENIED = "permission_denied"


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Per-setting capability status, so gaps are reported, not guessed at."""

    volume: CapabilityStatus
    brightness: CapabilityStatus


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Result of applying settings through an adapter.

    ``errors`` and ``rolled_back`` default to empty/false so this remains
    compatible with earlier callers that only constructed
    ``ApplyResult(applied=...)``.
    """

    applied: bool
    errors: tuple[str, ...] = field(default=())
    rolled_back: bool = False


class SettingsAdapter(Protocol):
    """Interface for mock and OS-specific settings adapters."""

    def read_current(self) -> DeviceSettings: ...

    def apply(self, settings: DeviceSettings) -> ApplyResult: ...

    def validate(self, settings: DeviceSettings) -> None: ...

    def capabilities(self) -> AdapterCapabilities: ...


class MockSettingsAdapter:
    """In-memory adapter that never changes host operating-system settings."""

    def __init__(self, initial: DeviceSettings) -> None:
        self._current = initial

    def read_current(self) -> DeviceSettings:
        return self._current

    def apply(self, settings: DeviceSettings) -> ApplyResult:
        self.validate(settings)
        self._current = settings
        return ApplyResult(applied=True)

    def validate(self, settings: DeviceSettings) -> None:
        DeviceSettings(volume=settings.volume, brightness=settings.brightness)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            volume=CapabilityStatus.AVAILABLE, brightness=CapabilityStatus.AVAILABLE
        )
