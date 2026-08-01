"""Atomic, auditable, idempotent candidate-to-profile promotion (M8).

Promotion requires explicit owner approval by default per HERMES.md; the
automatic-promotion configuration gate is rejected unconditionally by
``EnrollmentConfig`` until M14's liveness/consent/security prerequisites
exist. This module performs the safety-critical transaction: re-checking
for duplicate profiles at promotion time, transferring embeddings,
creating the profile, and recording an audit event, all in one commit.
"""

from __future__ import annotations

import sqlite3
from uuid import UUID

from face_profile.database.candidate_repository import CandidateRepository
from face_profile.database.models import CandidateStatus, Profile, ProfileStatus
from face_profile.database.repository import ProfileRepository, RecognitionEventRepository
from face_profile.recognition.matcher import find_nearest_profiles


class PromotionError(RuntimeError):
    """Raised when a candidate cannot be promoted safely."""


class CandidateNotReadyError(PromotionError):
    """Raised when a candidate has not reached ``READY_FOR_REVIEW``."""


class DuplicateProfileError(PromotionError):
    """Raised when a re-checked duplicate match blocks promotion."""


class CandidatePromoter:
    """Approve and promote one ``READY_FOR_REVIEW`` candidate to a profile."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        candidates: CandidateRepository,
        profiles: ProfileRepository,
        events: RecognitionEventRepository,
        duplicate_profile_threshold: float,
    ) -> None:
        self._connection = connection
        self._candidates = candidates
        self._profiles = profiles
        self._events = events
        self._duplicate_profile_threshold = duplicate_profile_threshold

    def promote(
        self,
        candidate_id: UUID,
        *,
        reviewed_by: str,
        correlation_id: str | None = None,
    ) -> Profile:
        """Approve and promote a candidate, or idempotently return its profile.

        A candidate already in ``PROMOTED`` status returns its existing
        profile without any further writes, so a retried approval call
        (for example after a network timeout) is safe.
        """

        candidate = self._candidates.get(candidate_id)
        if candidate.status is CandidateStatus.PROMOTED:
            if candidate.promoted_profile_id is None:
                raise PromotionError("promoted candidate is missing its profile reference")
            return self._profiles.get(candidate.promoted_profile_id)
        if candidate.status is not CandidateStatus.READY_FOR_REVIEW:
            raise CandidateNotReadyError(
                f"candidate is not ready for review (status={candidate.status})"
            )

        embeddings = [
            (metadata, self._candidates.get_embedding_vector(metadata.id))
            for metadata in self._candidates.list_embeddings(candidate_id)
        ]
        if not embeddings:
            raise PromotionError("candidate has no samples to promote")

        active_pairs = tuple(
            (profile.id, self._profiles.get_embedding_vector(metadata.id))
            for profile in self._profiles.list(status=ProfileStatus.ACTIVE, limit=1000)
            for metadata in self._profiles.list_embeddings(profile.id)
        )
        for _, embedding in embeddings:
            for match in find_nearest_profiles(embedding, active_pairs):
                if match.similarity >= self._duplicate_profile_threshold:
                    self._reject_as_duplicate(candidate_id, matched_profile_id=match.profile_id)

        try:
            profile = self._profiles.create(
                display_name=candidate.temporary_name,
                status=ProfileStatus.ACTIVE,
                metadata={"promoted_from_candidate": str(candidate_id)},
            )
            for metadata, embedding in embeddings:
                self._profiles.add_embedding(
                    profile.id, embedding, quality_score=metadata.quality_score
                )
            self._candidates.mark_promoted(
                candidate_id, profile_id=profile.id, reviewed_by=reviewed_by
            )
            self._events.record(
                event_type="ProfileCreated",
                profile_id=profile.id,
                candidate_id=candidate_id,
                correlation_id=correlation_id,
            )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return profile

    def _reject_as_duplicate(self, candidate_id: UUID, *, matched_profile_id: UUID) -> None:
        try:
            self._candidates.reject(candidate_id, reason="duplicate_profile_match")
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        raise DuplicateProfileError(f"candidate matches existing profile {matched_profile_id}")
