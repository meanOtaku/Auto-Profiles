"""Known-person recognition facade combining M5-M7 primitives.

This is the only M7 component that talks to the M6 database. It loads
active profiles' embeddings, searches for the nearest matches, applies the
threshold/margin decision, and stabilizes it per track. It never persists,
enrolls, merges, or selects an active user; those remain M8/M10 concerns.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from face_profile.database.models import ProfileStatus
from face_profile.database.repository import ProfileRepository
from face_profile.recognition.cache import RecognitionCache
from face_profile.recognition.decision import RecognitionDecision, decide
from face_profile.recognition.matcher import find_nearest_profiles
from face_profile.vision.embedding import FaceEmbedding


class KnownPersonRecognizer:
    """Recognize one track's embedding against currently active profiles."""

    def __init__(
        self,
        *,
        profiles: ProfileRepository,
        cache: RecognitionCache,
        threshold: float,
        margin: float,
    ) -> None:
        self._profiles = profiles
        self._cache = cache
        self._threshold = threshold
        self._margin = margin

    def recognize(
        self,
        track_id: int,
        embedding: FaceEmbedding,
        *,
        observed_at: datetime,
    ) -> RecognitionDecision:
        """Return the stabilized recognition decision for one observation.

        Loading every active profile's embeddings and decrypting each one
        on every call is a known, documented performance limitation;
        caching and batching are deferred to M15.
        """

        candidates = tuple(self._iter_active_candidate_embeddings())
        matches = find_nearest_profiles(embedding, candidates)
        raw_decision = decide(matches, threshold=self._threshold, margin=self._margin)
        return self._cache.observe(track_id, raw_decision, observed_at=observed_at)

    def track_ended(self, track_id: int) -> None:
        """Release cached recognition state for a track that has ended."""

        self._cache.discard(track_id)

    def _iter_active_candidate_embeddings(self) -> tuple[tuple[UUID, FaceEmbedding], ...]:
        pairs: list[tuple[UUID, FaceEmbedding]] = []
        for profile in self._profiles.list(status=ProfileStatus.ACTIVE, limit=1000):
            for metadata in self._profiles.list_embeddings(profile.id):
                pairs.append((profile.id, self._profiles.get_embedding_vector(metadata.id)))
        return tuple(pairs)
