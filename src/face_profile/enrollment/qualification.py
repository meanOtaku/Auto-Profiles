"""Pure candidate-qualification decision logic (no I/O).

Implements HERMES.md's Unknown-Person Policy checks that gate whether a
candidate may move from COLLECTING to READY_FOR_REVIEW. This module never
touches the database or decides promotion itself: reaching
READY_FOR_REVIEW still requires explicit owner approval by default
(``enrollment/promotion.py``), and automatic promotion is a separate,
disabled-by-default configuration gate this module does not implement.

The "minimum temporal spoof and replay checks" ROADMAP.md assigns to M8
are the internal-consistency and temporal-variability checks below. They
are deliberately conservative geometric/statistical heuristics over the
candidate's own collected embeddings, not a trained anti-spoof or liveness
model — that is M14's job, which HERMES explicitly describes as building
on these minimum checks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from face_profile.vision.embedding import FaceEmbedding, cosine_similarity


@dataclass(frozen=True, slots=True)
class QualificationResult:
    """Whether a candidate currently meets every configured promotion gate."""

    ready: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationThresholds:
    """Configured gates a candidate must pass before owner review."""

    minimum_samples: int
    minimum_observation_seconds: float
    minimum_quality: float
    minimum_internal_consistency: float
    maximum_internal_similarity: float
    duplicate_profile_threshold: float
    duplicate_candidate_threshold: float


def evaluate_candidate_qualification(
    *,
    sample_count: int,
    observation_seconds: float,
    aggregate_quality: float,
    embeddings: Sequence[FaceEmbedding],
    has_near_frontal_sample: bool,
    best_active_profile_similarity: float | None,
    best_other_candidate_similarity: float | None,
    thresholds: QualificationThresholds,
    liveness_ok: bool = True,
) -> QualificationResult:
    """Evaluate every configured gate and report every failing reason.

    ``embeddings`` must be the candidate's own collected samples (not
    external profiles); their pairwise similarity backs both the
    mixed-identity guard and the minimum temporal-variability check.
    ``liveness_ok`` defaults to True so callers that have not enabled M14
    liveness are unaffected; when M14 is enabled, a caller must pass
    whether every observed sample passed the configured liveness checks
    (HERMES.md: "failed liveness cannot create a permanent profile").
    """

    reasons: list[str] = []

    if sample_count < thresholds.minimum_samples:
        reasons.append("insufficient_samples")
    if observation_seconds < thresholds.minimum_observation_seconds:
        reasons.append("insufficient_observation_duration")
    if aggregate_quality < thresholds.minimum_quality:
        reasons.append("insufficient_quality")
    if not has_near_frontal_sample:
        reasons.append("no_near_frontal_sample")
    if not liveness_ok:
        reasons.append("liveness_failed")

    pairwise = _pairwise_similarities(embeddings)
    if pairwise:
        if min(pairwise) < thresholds.minimum_internal_consistency:
            reasons.append("inconsistent_embeddings")
        if max(pairwise) > thresholds.maximum_internal_similarity:
            reasons.append("insufficient_temporal_variability")

    if (
        best_active_profile_similarity is not None
        and best_active_profile_similarity >= thresholds.duplicate_profile_threshold
    ):
        reasons.append("matches_existing_profile")
    if (
        best_other_candidate_similarity is not None
        and best_other_candidate_similarity >= thresholds.duplicate_candidate_threshold
    ):
        reasons.append("matches_existing_candidate")

    return QualificationResult(ready=len(reasons) == 0, reasons=tuple(reasons))


def _pairwise_similarities(embeddings: Sequence[FaceEmbedding]) -> tuple[float, ...]:
    return tuple(cosine_similarity(a, b) for a, b in combinations(embeddings, 2))
