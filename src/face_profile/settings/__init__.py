"""Portable settings boundary and deterministic test adapter."""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Result of applying settings through an adapter."""

    applied: bool


class SettingsAdapter(Protocol):
    """Interface for mock and OS-specific settings adapters."""

    def read_current(self) -> DeviceSettings: ...

    def apply(self, settings: DeviceSettings) -> ApplyResult: ...

    def validate(self, settings: DeviceSettings) -> None: ...


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
