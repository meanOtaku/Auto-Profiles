"""Nearest-profile similarity search over in-memory candidate embeddings.

M7 searches only embeddings the caller explicitly supplies; it does not
query the database itself, keeping this module testable with synthetic
vectors and reusable independent of M6's storage layer.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from face_profile.recognition.decision import ProfileMatch
from face_profile.vision.embedding import FaceEmbedding, cosine_similarity


def find_nearest_profiles(
    query: FaceEmbedding,
    candidates: Iterable[tuple[UUID, FaceEmbedding]],
) -> tuple[ProfileMatch, ...]:
    """Rank profiles by their best-matching embedding's similarity to ``query``.

    ``candidates`` may list a profile more than once (one entry per stored
    embedding); only that profile's single highest similarity is kept, so
    one profile never occupies more than one rank. Results are sorted by
    descending similarity, with ties broken by profile ID for determinism.
    """

    best_per_profile: dict[UUID, float] = {}
    for profile_id, embedding in candidates:
        similarity = cosine_similarity(query, embedding)
        current_best = best_per_profile.get(profile_id)
        if current_best is None or similarity > current_best:
            best_per_profile[profile_id] = similarity
    matches = tuple(
        ProfileMatch(profile_id=profile_id, similarity=similarity)
        for profile_id, similarity in best_per_profile.items()
    )
    return tuple(sorted(matches, key=lambda match: (-match.similarity, str(match.profile_id))))
