"""Construct the M10 active-user selector from validated configuration."""

from __future__ import annotations

from face_profile.config import ActiveUserConfig
from face_profile.presence.active_user import ActiveUserSelector, ScoreWeights


def create_active_user_selector(config: ActiveUserConfig) -> ActiveUserSelector | None:
    """Create the configured selector only when M10 is explicitly enabled."""

    if not config.enabled:
        return None
    weights = ScoreWeights(
        face_size=config.face_size_weight,
        centre_proximity=config.centre_weight,
        duration=config.duration_weight,
        confidence=config.confidence_weight,
        priority=config.priority_weight,
        duration_saturation_seconds=config.duration_saturation_seconds,
        priority_normalization_scale=config.priority_normalization_scale,
    )
    return ActiveUserSelector(
        weights=weights,
        switch_margin=config.switch_margin,
        stability_duration_seconds=config.stability_duration_seconds,
        switch_cooldown_seconds=config.switch_cooldown_seconds,
        min_visible_duration_seconds=config.min_visible_duration_seconds,
        leaving_grace_seconds=config.leaving_grace_seconds,
    )
