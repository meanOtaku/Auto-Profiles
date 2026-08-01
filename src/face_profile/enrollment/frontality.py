"""Near-frontal sample detection for enrollment qualification.

A minimal, self-contained pose check independent of M4's quality-gate
acceptance bounds (which are looser, since M4 only needs to reject clearly
unusable crops). HERMES.md requires "at least one near-frontal sample"
before a candidate may be promoted, which needs its own, typically
tighter, geometric bound.
"""

from __future__ import annotations

from math import atan2, degrees, hypot

from face_profile.vision.detection import FaceLandmarks


def is_near_frontal(
    landmarks: FaceLandmarks,
    *,
    max_roll_degrees: float,
    max_yaw_asymmetry: float,
) -> bool:
    """Return True when eye-line roll and nose-symmetry yaw are both small."""

    dx = landmarks.left_eye.x - landmarks.right_eye.x
    dy = landmarks.left_eye.y - landmarks.right_eye.y
    roll_degrees = abs(degrees(atan2(dy, dx)))
    if roll_degrees > max_roll_degrees:
        return False
    eye_midpoint_x = (landmarks.left_eye.x + landmarks.right_eye.x) / 2.0
    eye_distance = hypot(dx, dy)
    yaw_asymmetry = abs(landmarks.nose.x - eye_midpoint_x) / max(eye_distance, 1e-6)
    return yaw_asymmetry <= max_yaw_asymmetry
