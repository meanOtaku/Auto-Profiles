"""Per-track temporal confirmation to prevent identity flicker.

M7 must not let a single ambiguous or unlucky frame change a track's
recognized identity, and must require repeated consistent evidence before
treating a match as confirmed. This module holds only small, bounded,
in-memory per-track state (candidate/confirmed profile ID and counters) —
no pixels, embeddings, or durable storage. It is unrelated to M10's
system-wide active-user selection, which operates across tracks and
profiles rather than stabilizing one track's own identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from face_profile.recognition.decision import RecognitionDecision, RecognitionState


class RecognitionCacheError(RuntimeError):
    """Raised when the recognition cache is used outside its bounds."""


@dataclass(slots=True)
class _TrackRecognitionState:
    confirmed_profile_id: UUID | None
    candidate_profile_id: UUID | None
    consistent_count: int
    first_candidate_observed_at: datetime | None
    last_observed_at: datetime | None


class RecognitionCache:
    """Stabilize per-track recognition decisions across repeated observations.

    Once a track's identity is confirmed, it remains confirmed (sticky)
    through transient ``UNKNOWN``/``AMBIGUOUS`` observations, so a brief
    blurry or side-angle frame does not flicker the track's identity away.
    A different profile only replaces the confirmed one after it
    accumulates its own configured run of consistent ``POSSIBLE_MATCH``
    observations, all within the configured confirmation window.
    """

    def __init__(
        self,
        *,
        min_consistent_observations: int,
        confirmation_window_seconds: float,
        max_tracks: int,
    ) -> None:
        if min_consistent_observations < 1:
            raise ValueError("min_consistent_observations must be positive")
        if confirmation_window_seconds <= 0.0:
            raise ValueError("confirmation_window_seconds must be positive")
        if max_tracks < 1:
            raise ValueError("max_tracks must be positive")
        self._min_consistent_observations = min_consistent_observations
        self._confirmation_window_seconds = confirmation_window_seconds
        self._max_tracks = max_tracks
        self._tracks: dict[int, _TrackRecognitionState] = {}

    def observe(
        self,
        track_id: int,
        decision: RecognitionDecision,
        *,
        observed_at: datetime,
    ) -> RecognitionDecision:
        """Fold in one single-frame decision and return the stabilized result."""

        state = self._tracks.get(track_id)
        if state is None:
            if len(self._tracks) >= self._max_tracks:
                raise RecognitionCacheError("maximum tracked recognition count reached")
            state = _TrackRecognitionState(None, None, 0, None, None)
            self._tracks[track_id] = state

        if decision.state is RecognitionState.POSSIBLE_MATCH:
            self._observe_possible_match(state, decision, observed_at)
        state.last_observed_at = observed_at

        if state.confirmed_profile_id is not None:
            return RecognitionDecision(
                state=RecognitionState.CONFIRMED_MATCH,
                profile_id=state.confirmed_profile_id,
                similarity=decision.similarity,
                second_best_similarity=decision.second_best_similarity,
                reason="confirmed_over_time",
            )
        return decision

    def _observe_possible_match(
        self,
        state: _TrackRecognitionState,
        decision: RecognitionDecision,
        observed_at: datetime,
    ) -> None:
        if decision.profile_id == state.confirmed_profile_id:
            return
        window_expired = (
            state.first_candidate_observed_at is not None
            and (observed_at - state.first_candidate_observed_at).total_seconds()
            > self._confirmation_window_seconds
        )
        if decision.profile_id != state.candidate_profile_id or window_expired:
            state.candidate_profile_id = decision.profile_id
            state.consistent_count = 1
            state.first_candidate_observed_at = observed_at
            return
        state.consistent_count += 1
        if state.consistent_count >= self._min_consistent_observations:
            state.confirmed_profile_id = decision.profile_id
            state.candidate_profile_id = None
            state.consistent_count = 0
            state.first_candidate_observed_at = None

    def discard(self, track_id: int) -> None:
        """Drop all cached recognition state for a track that has ended."""

        self._tracks.pop(track_id, None)
