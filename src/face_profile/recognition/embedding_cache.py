"""Bounded, TTL-refreshed cache of active-profile embeddings (M15).

M7's ``KnownPersonRecognizer`` originally decrypted every active
profile's every embedding on every ``recognize()`` call — a documented
O(profiles x embeddings) performance limitation (see ADR 0012/0013).
This cache amortizes that cost across a configured refresh interval
instead of paying it every frame, without changing the recognition
decision logic itself.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from face_profile.database.models import ProfileStatus
from face_profile.database.repository import ProfileRepository
from face_profile.vision.embedding import FaceEmbedding


class ActiveProfileEmbeddingCache:
    """Refreshes the active-profile embedding set at most once per interval."""

    def __init__(self, *, profiles: ProfileRepository, refresh_interval_seconds: float) -> None:
        if refresh_interval_seconds <= 0.0:
            raise ValueError("refresh_interval_seconds must be positive")
        self._profiles = profiles
        self._refresh_interval_seconds = refresh_interval_seconds
        self._cached: tuple[tuple[UUID, FaceEmbedding], ...] = ()
        self._last_refreshed_at: datetime | None = None

    def get(self, *, now: datetime) -> tuple[tuple[UUID, FaceEmbedding], ...]:
        """Return the cached embedding set, refreshing it if the interval elapsed."""

        needs_refresh = (
            self._last_refreshed_at is None
            or (now - self._last_refreshed_at).total_seconds() >= self._refresh_interval_seconds
        )
        if needs_refresh:
            self._refresh()
            self._last_refreshed_at = now
        return self._cached

    def invalidate(self) -> None:
        """Force the next :meth:`get` call to refresh, e.g. after a new promotion."""

        self._last_refreshed_at = None

    def _refresh(self) -> None:
        pairs: list[tuple[UUID, FaceEmbedding]] = []
        for profile in self._profiles.list(status=ProfileStatus.ACTIVE, limit=1000):
            for metadata in self._profiles.list_embeddings(profile.id):
                pairs.append((profile.id, self._profiles.get_embedding_vector(metadata.id)))
        self._cached = tuple(pairs)
