"""Single-observation recognition decision logic.

M7 turns one embedding's nearest-profile matches into a decision using a
configured similarity threshold and margin, per ARCHITECTURE.md section 8.
This module makes no temporal or per-track judgment; that is
``recognition/cache.py``'s responsibility. It never mutates profile,
candidate, or persistence state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class RecognitionState(StrEnum):
    """Outcome of comparing one observation against known profiles.

    ``POSSIBLE_MATCH`` is a single-observation result awaiting temporal
    confirmation; only ``recognition/cache.py`` may promote a track to
    ``CONFIRMED_MATCH``. Quality and liveness rejection are decided by M4
    and M14 respectively, upstream of this module, so they are not states
    here.
    """

    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    POSSIBLE_MATCH = "possible_match"
    CONFIRMED_MATCH = "confirmed_match"


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    """One profile's best similarity against a query embedding."""

    profile_id: UUID
    similarity: float


@dataclass(frozen=True, slots=True)
class RecognitionDecision:
    """A recognition outcome for one observation or one stabilized track."""

    state: RecognitionState
    profile_id: UUID | None
    similarity: float | None
    second_best_similarity: float | None
    reason: str


def decide(
    matches: tuple[ProfileMatch, ...],
    *,
    threshold: float,
    margin: float,
) -> RecognitionDecision:
    """Apply the threshold/margin rule to one observation's ranked matches.

    ``matches`` must already be sorted by descending similarity; the two
    highest-ranked entries determine the outcome.
    """

    if not -1.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between -1.0 and 1.0")
    if margin < 0.0:
        raise ValueError("margin must not be negative")
    if len(matches) == 0:
        return RecognitionDecision(
            state=RecognitionState.UNKNOWN,
            profile_id=None,
            similarity=None,
            second_best_similarity=None,
            reason="no_known_profiles",
        )
    best = matches[0]
    second_best = matches[1] if len(matches) > 1 else None
    second_similarity = second_best.similarity if second_best is not None else None

    if best.similarity < threshold:
        return RecognitionDecision(
            state=RecognitionState.UNKNOWN,
            profile_id=None,
            similarity=best.similarity,
            second_best_similarity=second_similarity,
            reason="below_threshold",
        )
    if second_best is not None and (best.similarity - second_best.similarity) < margin:
        return RecognitionDecision(
            state=RecognitionState.AMBIGUOUS,
            profile_id=None,
            similarity=best.similarity,
            second_best_similarity=second_similarity,
            reason="margin_too_small",
        )
    return RecognitionDecision(
        state=RecognitionState.POSSIBLE_MATCH,
        profile_id=best.profile_id,
        similarity=best.similarity,
        second_best_similarity=second_similarity,
        reason="best_match_above_threshold",
    )
