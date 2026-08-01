"""Construct the M7 recognizer from validated configuration."""

from __future__ import annotations

from face_profile.config import AppConfig
from face_profile.database.repository import ProfileRepository
from face_profile.recognition.cache import RecognitionCache
from face_profile.recognition.recognizer import KnownPersonRecognizer


def create_recognizer(
    config: AppConfig,
    profiles: ProfileRepository,
) -> KnownPersonRecognizer | None:
    """Build the configured recognizer, or return None unless explicitly enabled.

    Recognition additionally requires ``embedding.enabled`` since it
    consumes M5's configured similarity threshold/margin and compares
    same-model embeddings only.
    """

    if not config.recognition.enabled or not config.embedding.enabled:
        return None
    cache = RecognitionCache(
        min_consistent_observations=config.recognition.min_consistent_observations,
        confirmation_window_seconds=config.recognition.confirmation_window_seconds,
        max_tracks=config.recognition.max_tracks,
    )
    return KnownPersonRecognizer(
        profiles=profiles,
        cache=cache,
        threshold=config.embedding.similarity_threshold,
        margin=config.embedding.similarity_margin,
    )
