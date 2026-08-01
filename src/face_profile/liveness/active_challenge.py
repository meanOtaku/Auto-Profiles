"""Optional active liveness challenge: verify a requested head-turn occurred.

A minimal, deterministic verifier over a caller-supplied sequence of
signed yaw proxies (the same dimensionless nose/eye-midpoint asymmetry
``enrollment/frontality.py`` computes, signed rather than absolute). It
does not capture frames or orchestrate a challenge UI itself — a future
daemon/UI workflow owns prompting the user and collecting the sequence;
this module only judges whether a supplied sequence satisfies one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from face_profile.vision.detection import FaceLandmarks


class ChallengeDirection(StrEnum):
    """Requested head-turn direction for an active liveness challenge."""

    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class ActiveChallengeResult:
    """Whether a supplied landmark sequence satisfied the requested challenge."""

    passed: bool
    max_signed_yaw: float
    reason: str


def signed_yaw_proxy(landmarks: FaceLandmarks) -> float:
    """Return a signed, dimensionless nose/eye-midpoint asymmetry proxy.

    Positive values indicate the nose is displaced toward the subject's
    left in image coordinates; this is a geometric proxy, not a
    calibrated degree measurement.
    """

    eye_midpoint_x = (landmarks.left_eye.x + landmarks.right_eye.x) / 2.0
    eye_distance = max(
        1e-6,
        (
            (landmarks.left_eye.x - landmarks.right_eye.x) ** 2
            + (landmarks.left_eye.y - landmarks.right_eye.y) ** 2
        )
        ** 0.5,
    )
    return float((landmarks.nose.x - eye_midpoint_x) / eye_distance)


def verify_turn_challenge(
    yaw_sequence: Sequence[float],
    *,
    direction: ChallengeDirection,
    min_yaw_delta: float,
) -> ActiveChallengeResult:
    """Check that a sequence starts near-frontal and reaches the requested turn.

    ``yaw_sequence`` must be ordered by capture time. The sequence must
    begin close to frontal (guarding against a pre-turned static replay
    already satisfying the challenge) and later reach the requested
    direction's sign with at least ``min_yaw_delta`` magnitude.
    """

    if len(yaw_sequence) < 2:
        return ActiveChallengeResult(
            passed=False, max_signed_yaw=0.0, reason="insufficient_samples"
        )
    if abs(yaw_sequence[0]) >= min_yaw_delta / 2.0:
        return ActiveChallengeResult(
            passed=False, max_signed_yaw=yaw_sequence[0], reason="did_not_start_frontal"
        )
    signed_extreme = float(max(yaw_sequence, key=abs))
    required_sign = 1.0 if direction is ChallengeDirection.LEFT else -1.0
    reached = signed_extreme * required_sign >= min_yaw_delta
    if not reached:
        return ActiveChallengeResult(
            passed=False, max_signed_yaw=signed_extreme, reason="requested_turn_not_reached"
        )
    return ActiveChallengeResult(
        passed=True, max_signed_yaw=signed_extreme, reason="turn_confirmed"
    )
