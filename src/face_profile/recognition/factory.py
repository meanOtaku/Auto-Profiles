"""Construct the M7 recognizer from validated configuration."""

from __future__ import annotations

from face_profile.config import AppConfig
from face_profile.database.repository import ProfileRepository
from face_profile.recognition.cache import RecognitionCache
from face_profile.recognition.embedding_cache import ActiveProfileEmbeddingCache
from face_profile.recognition.recognizer import KnownPersonRecognizer


def create_recognizer(
    config: AppConfig,
    profiles: ProfileRepository,
) -> KnownPersonRecognizer | None:
    """Build the configured recognizer, or return None unless explicitly enabled.

    Recognition additionally requires ``embedding.enabled`` since it
    consumes M5's configured similarity threshold/margin and compares
    same-model embeddings only. The M15 embedding cache is always
    constructed alongside an enabled recognizer, amortizing the decrypt
    cost documented in ADR 0012/0013 across
    ``embedding_cache_refresh_seconds`` instead of every call.
    """

    if not config.recognition.enabled or not config.embedding.enabled:
        return None
    cache = RecognitionCache(
        min_consistent_observations=config.recognition.min_consistent_observations,
        confirmation_window_seconds=config.recognition.confirmation_window_seconds,
        max_tracks=config.recognition.max_tracks,
    )
    embedding_cache = ActiveProfileEmbeddingCache(
        profiles=profiles,
        refresh_interval_seconds=config.recognition.embedding_cache_refresh_seconds,
    )
    return KnownPersonRecognizer(
        profiles=profiles,
        cache=cache,
        threshold=config.embedding.similarity_threshold,
        margin=config.embedding.similarity_margin,
        embedding_cache=embedding_cache,
    )
