"""Stable active-user selection: scoring, hysteresis, and switch cooldown.

M10 selects at most one profile to control shared device settings from
among the currently *recognized* (M7 ``CONFIRMED_MATCH``) tracks. It does
not decide identity itself (M7) and does not apply settings (M9/M11); it
only produces ``ActiveUserDecision`` values a caller uses to trigger
settings application. Unknown or merely possible-match tracks are simply
not eligible input: personalization always targets a known profile ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

# Guards the switch-margin comparison against floating-point summation
# error: weighted scores are sums of several float products, so a
# challenger whose true score sits exactly at the configured margin can
# otherwise land a few ULPs below it and be silently ignored.
_SCORE_EPSILON = 1e-9


class ActiveUserState(StrEnum):
    """HERMES.md's active-user lifecycle."""

    NONE = "none"
    CANDIDATE_ACTIVE = "candidate_active"
    ACTIVE = "active"
    LEAVING = "leaving"


@dataclass(frozen=True, slots=True)
class PresenceCandidate:
    """One recognized track's inputs to the active-user score for one update."""

    track_id: int
    profile_id: UUID
    normalized_face_size: float
    centre_proximity: float
    visible_duration_seconds: float
    recognition_confidence: float
    profile_priority: int

    def __post_init__(self) -> None:
        for name, value in (
            ("normalized_face_size", self.normalized_face_size),
            ("centre_proximity", self.centre_proximity),
            ("recognition_confidence", self.recognition_confidence),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0.0 and 1.0")
        if self.visible_duration_seconds < 0.0:
            raise ValueError("visible_duration_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class ActiveUserDecision:
    """The selector's output for one update: who, if anyone, is active."""

    state: ActiveUserState
    profile_id: UUID | None
    score: float | None
    active_since: datetime | None


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """Configured weights and normalization scales for :func:`compute_score`."""

    face_size: float
    centre_proximity: float
    duration: float
    confidence: float
    priority: float
    duration_saturation_seconds: float
    priority_normalization_scale: float


def compute_score(candidate: PresenceCandidate, weights: ScoreWeights) -> float:
    """Compute the weighted active-user score documented in ARCHITECTURE.md §10."""

    normalized_duration = min(
        1.0, candidate.visible_duration_seconds / weights.duration_saturation_seconds
    )
    normalized_priority = min(
        1.0, max(0.0, candidate.profile_priority / weights.priority_normalization_scale)
    )
    return (
        weights.face_size * candidate.normalized_face_size
        + weights.centre_proximity * candidate.centre_proximity
        + weights.duration * normalized_duration
        + weights.confidence * candidate.recognition_confidence
        + weights.priority * normalized_priority
    )


class ActiveUserSelector:
    """Stateful policy selecting one stable active profile across updates.

    The current active user retains its advantage until a challenger's
    score exceeds it by ``switch_margin`` continuously for
    ``stability_duration_seconds``, and even then a switch is refused
    until ``switch_cooldown_seconds`` has elapsed since the last switch.
    A brief candidate that never sustains the lead never takes over.
    """

    def __init__(
        self,
        *,
        weights: ScoreWeights,
        switch_margin: float,
        stability_duration_seconds: float,
        switch_cooldown_seconds: float,
        min_visible_duration_seconds: float,
        leaving_grace_seconds: float,
    ) -> None:
        if switch_margin < 0.0:
            raise ValueError("switch_margin must not be negative")
        if stability_duration_seconds <= 0.0:
            raise ValueError("stability_duration_seconds must be positive")
        if switch_cooldown_seconds < 0.0:
            raise ValueError("switch_cooldown_seconds must not be negative")
        if min_visible_duration_seconds < 0.0:
            raise ValueError("min_visible_duration_seconds must not be negative")
        if leaving_grace_seconds < 0.0:
            raise ValueError("leaving_grace_seconds must not be negative")
        self._weights = weights
        self._switch_margin = switch_margin
        self._stability_duration_seconds = stability_duration_seconds
        self._switch_cooldown_seconds = switch_cooldown_seconds
        self._min_visible_duration_seconds = min_visible_duration_seconds
        self._leaving_grace_seconds = leaving_grace_seconds

        self._active_profile_id: UUID | None = None
        self._active_since: datetime | None = None
        self._missing_since: datetime | None = None
        self._pending_profile_id: UUID | None = None
        self._pending_since: datetime | None = None
        self._last_switch_at: datetime | None = None

    def update(
        self, candidates: tuple[PresenceCandidate, ...], *, now: datetime
    ) -> ActiveUserDecision:
        """Fold in one snapshot of currently recognized, visible tracks."""

        eligible = tuple(
            c
            for c in candidates
            if c.visible_duration_seconds >= self._min_visible_duration_seconds
        )
        scored = tuple((c, compute_score(c, self._weights)) for c in eligible)
        best = max(scored, key=lambda pair: pair[1], default=None)

        if self._active_profile_id is None:
            return self._update_from_none(best, now)
        return self._update_with_active(scored, best, now)

    def _update_from_none(
        self, best: tuple[PresenceCandidate, float] | None, now: datetime
    ) -> ActiveUserDecision:
        if best is None:
            self._pending_profile_id = None
            self._pending_since = None
            return ActiveUserDecision(
                state=ActiveUserState.NONE, profile_id=None, score=None, active_since=None
            )
        candidate, score = best
        if self._pending_profile_id != candidate.profile_id:
            self._pending_profile_id = candidate.profile_id
            self._pending_since = now
            return ActiveUserDecision(
                state=ActiveUserState.CANDIDATE_ACTIVE,
                profile_id=candidate.profile_id,
                score=score,
                active_since=None,
            )
        elapsed = (now - self._pending_since).total_seconds() if self._pending_since else 0.0
        if elapsed < self._stability_duration_seconds:
            return ActiveUserDecision(
                state=ActiveUserState.CANDIDATE_ACTIVE,
                profile_id=candidate.profile_id,
                score=score,
                active_since=None,
            )
        self._activate(candidate.profile_id, now)
        return ActiveUserDecision(
            state=ActiveUserState.ACTIVE,
            profile_id=candidate.profile_id,
            score=score,
            active_since=self._active_since,
        )

    def _update_with_active(
        self,
        scored: tuple[tuple[PresenceCandidate, float], ...],
        best: tuple[PresenceCandidate, float] | None,
        now: datetime,
    ) -> ActiveUserDecision:
        active_score = next(
            (score for c, score in scored if c.profile_id == self._active_profile_id), None
        )
        if active_score is None:
            return self._update_active_missing(best, now)
        self._missing_since = None

        if best is not None and best[0].profile_id == self._active_profile_id:
            self._pending_profile_id = None
            self._pending_since = None
            return self._still_active(active_score)

        if best is None or (best[1] - active_score) < self._switch_margin - _SCORE_EPSILON:
            self._pending_profile_id = None
            self._pending_since = None
            return self._still_active(active_score)

        return self._consider_challenger(best, active_score, now)

    def _consider_challenger(
        self, best: tuple[PresenceCandidate, float], active_score: float, now: datetime
    ) -> ActiveUserDecision:
        challenger, challenger_score = best
        if self._pending_profile_id != challenger.profile_id:
            self._pending_profile_id = challenger.profile_id
            self._pending_since = now
            return self._still_active(active_score)

        stable_long_enough = (
            self._pending_since is not None
            and (now - self._pending_since).total_seconds() >= self._stability_duration_seconds
        )
        cooldown_elapsed = (
            self._last_switch_at is None
            or (now - self._last_switch_at).total_seconds() >= self._switch_cooldown_seconds
        )
        if not (stable_long_enough and cooldown_elapsed):
            return self._still_active(active_score)

        self._activate(challenger.profile_id, now)
        return ActiveUserDecision(
            state=ActiveUserState.ACTIVE,
            profile_id=challenger.profile_id,
            score=challenger_score,
            active_since=self._active_since,
        )

    def _update_active_missing(
        self, best: tuple[PresenceCandidate, float] | None, now: datetime
    ) -> ActiveUserDecision:
        if self._missing_since is None:
            self._missing_since = now
        elapsed = (now - self._missing_since).total_seconds()
        if elapsed < self._leaving_grace_seconds:
            return ActiveUserDecision(
                state=ActiveUserState.LEAVING,
                profile_id=self._active_profile_id,
                score=None,
                active_since=self._active_since,
            )
        self._deactivate()
        return self._update_from_none(best, now)

    def _still_active(self, active_score: float) -> ActiveUserDecision:
        return ActiveUserDecision(
            state=ActiveUserState.ACTIVE,
            profile_id=self._active_profile_id,
            score=active_score,
            active_since=self._active_since,
        )

    def _activate(self, profile_id: UUID, now: datetime) -> None:
        self._active_profile_id = profile_id
        self._active_since = now
        self._last_switch_at = now
        self._missing_since = None
        self._pending_profile_id = None
        self._pending_since = None

    def _deactivate(self) -> None:
        self._active_profile_id = None
        self._active_since = None
        self._missing_since = None
