"""Construct the configured settings adapter, wrapped for rate limiting."""

from __future__ import annotations

from face_profile.config import SettingsConfig
from face_profile.settings import DeviceSettings, MockSettingsAdapter, SettingsAdapter
from face_profile.settings.linux import LinuxSettingsAdapter
from face_profile.settings.rate_limit import RateLimitedSettingsAdapter


def create_settings_adapter(config: SettingsConfig, *, initial: DeviceSettings) -> SettingsAdapter:
    """Build the configured adapter, always rate-limited.

    Unlike other M1-M8 factories, this one never returns ``None``: a
    settings adapter is always available (the in-memory mock), since
    recognition and profile code must never be left without a safe
    settings boundary to depend on. Only the *real* backend selection is
    gated by explicit configuration.
    """

    if config.backend == "mock":
        base: SettingsAdapter = MockSettingsAdapter(initial)
    else:
        base = LinuxSettingsAdapter()
    return RateLimitedSettingsAdapter(
        base, min_apply_interval_seconds=config.min_apply_interval_seconds
    )
