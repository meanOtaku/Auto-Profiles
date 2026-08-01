"""Per-track candidate lifecycle orchestration for unrecognized detections.

``CandidateManager`` decides when an M7 ``UNKNOWN`` recognition observation
should create or extend a temporary candidate, evaluates HERMES.md's
unknown-person policy after each sample, and marks candidates ready for
owner review. It never promotes a candidate itself (``enrollment/
promotion.py`` owns that, gated on explicit approval) and never creates a
permanent profile.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from face_profile.config import EnrollmentConfig
from face_profile.database.candidate_repository import CandidateRepository
from face_profile.database.models import Candidate, CandidateStatus, ProfileStatus
from face_profile.database.repository import ProfileRepository
from face_profile.enrollment.frontality import is_near_frontal
from face_profile.enrollment.qualification import (
    QualificationResult,
    QualificationThresholds,
    evaluate_candidate_qualification,
)
from face_profile.recognition.matcher import find_nearest_profiles
from face_profile.vision.detection import FaceLandmarks
from face_profile.vision.embedding import FaceEmbedding


class CandidateManagerError(RuntimeError):
    """Raised when candidate observation is used outside its bounds."""


class CandidateManager:
    """Create, extend, qualify, and expire candidates for unrecognized tracks."""

    def __init__(
        self,
        *,
        candidates: CandidateRepository,
        profiles: ProfileRepository,
        config: EnrollmentConfig,
        max_tracked: int = 1000,
    ) -> None:
        self._candidates = candidates
        self._profiles = profiles
        self._config = config
        self._max_tracked = max_tracked
        self._track_candidates: dict[int, UUID] = {}
        self._track_frontal_seen: dict[int, bool] = {}

    def observe_unknown(
        self,
        track_id: int,
        embedding: FaceEmbedding,
        landmarks: FaceLandmarks,
        *,
        quality_score: float,
        observed_at: datetime,
        source_camera_id: str | None = None,
    ) -> Candidate:
        """Fold in one unrecognized (M7 ``UNKNOWN``) observation for a track.

        A one-frame observation never crosses ``minimum_samples``, so it
        can never reach ``READY_FOR_REVIEW`` and can never create a
        permanent profile.
        """

        candidate = self._resolve_or_start_candidate(track_id, observed_at)
        if candidate.status is not CandidateStatus.COLLECTING:
            return candidate

        self._candidates.add_sample(
            candidate.id,
            embedding,
            quality_score=quality_score,
            observed_at=observed_at,
            source_camera_id=source_camera_id,
            source_track_id=track_id,
            max_samples=self._config.maximum_samples_per_candidate,
        )
        if is_near_frontal(
            landmarks,
            max_roll_degrees=self._config.near_frontal_max_roll_degrees,
            max_yaw_asymmetry=self._config.near_frontal_max_yaw_asymmetry,
        ):
            self._track_frontal_seen[track_id] = True

        refreshed = self._candidates.get(candidate.id)
        result = self.evaluate(track_id, refreshed)
        if result.ready:
            self._candidates.mark_ready_for_review(candidate.id)
        return self._candidates.get(candidate.id)

    def _resolve_or_start_candidate(self, track_id: int, observed_at: datetime) -> Candidate:
        candidate_id = self._track_candidates.get(track_id)
        candidate = self._candidates.get(candidate_id) if candidate_id is not None else None
        if candidate is not None and candidate.status in (
            CandidateStatus.EXPIRED,
            CandidateStatus.REJECTED,
        ):
            self._forget_track(track_id)
            candidate = None
        if candidate is None:
            if len(self._track_candidates) >= self._max_tracked:
                raise CandidateManagerError("maximum tracked candidate count reached")
            candidate = self._candidates.create_candidate(
                first_seen_at=observed_at, name_prefix=self._config.default_name_prefix
            )
            self._track_candidates[track_id] = candidate.id
            self._track_frontal_seen[track_id] = False
        return candidate

    def evaluate(self, track_id: int, candidate: Candidate) -> QualificationResult:
        """Evaluate whether a candidate currently meets every promotion gate."""

        embeddings = [
            self._candidates.get_embedding_vector(metadata.id)
            for metadata in self._candidates.list_embeddings(candidate.id)
        ]
        observation_seconds = (candidate.last_seen_at - candidate.first_seen_at).total_seconds()
        active_pairs = self._active_profile_embeddings()
        other_candidate_pairs = self._other_candidate_embeddings(exclude=candidate.id)
        thresholds = QualificationThresholds(
            minimum_samples=self._config.minimum_samples,
            minimum_observation_seconds=self._config.minimum_observation_seconds,
            minimum_quality=self._config.minimum_quality,
            minimum_internal_consistency=self._config.minimum_internal_consistency,
            maximum_internal_similarity=self._config.maximum_internal_similarity,
            duplicate_profile_threshold=self._config.duplicate_profile_threshold,
            duplicate_candidate_threshold=self._config.duplicate_candidate_threshold,
        )
        return evaluate_candidate_qualification(
            sample_count=candidate.sample_count,
            observation_seconds=observation_seconds,
            aggregate_quality=candidate.aggregate_quality,
            embeddings=embeddings,
            has_near_frontal_sample=self._track_frontal_seen.get(track_id, False),
            best_active_profile_similarity=_best_similarity(embeddings, active_pairs),
            best_other_candidate_similarity=_best_similarity(embeddings, other_candidate_pairs),
            thresholds=thresholds,
        )

    def expire_stale(self, *, now: datetime) -> tuple[UUID, ...]:
        """Expire every candidate whose last sample is older than the configured limit."""

        expired = self._candidates.expire_stale(
            now=now, expiry_seconds=self._config.candidate_expiry_seconds
        )
        expired_set = set(expired)
        for track_id, candidate_id in list(self._track_candidates.items()):
            if candidate_id in expired_set:
                self._forget_track(track_id)
        return expired

    def track_ended(self, track_id: int) -> None:
        """Release in-memory track association for a track that has ended.

        The candidate row itself is left untouched: it may already be
        ``READY_FOR_REVIEW`` awaiting owner action, and losing track
        continuity must not silently discard collected evidence.
        """

        self._forget_track(track_id)

    def _forget_track(self, track_id: int) -> None:
        self._track_candidates.pop(track_id, None)
        self._track_frontal_seen.pop(track_id, None)

    def _active_profile_embeddings(self) -> tuple[tuple[UUID, FaceEmbedding], ...]:
        pairs: list[tuple[UUID, FaceEmbedding]] = []
        for profile in self._profiles.list(status=ProfileStatus.ACTIVE, limit=1000):
            for metadata in self._profiles.list_embeddings(profile.id):
                pairs.append((profile.id, self._profiles.get_embedding_vector(metadata.id)))
        return tuple(pairs)

    def _other_candidate_embeddings(
        self, *, exclude: UUID
    ) -> tuple[tuple[UUID, FaceEmbedding], ...]:
        pairs: list[tuple[UUID, FaceEmbedding]] = []
        others = self._candidates.list(
            status=CandidateStatus.COLLECTING, limit=1000
        ) + self._candidates.list(status=CandidateStatus.READY_FOR_REVIEW, limit=1000)
        for other in others:
            if other.id == exclude:
                continue
            for metadata in self._candidates.list_embeddings(other.id):
                pairs.append((other.id, self._candidates.get_embedding_vector(metadata.id)))
        return tuple(pairs)


def _best_similarity(
    embeddings: list[FaceEmbedding], pairs: tuple[tuple[UUID, FaceEmbedding], ...]
) -> float | None:
    if not pairs:
        return None
    best: float | None = None
    for embedding in embeddings:
        matches = find_nearest_profiles(embedding, pairs)
        if matches and (best is None or matches[0].similarity > best):
            best = matches[0].similarity
    return best
