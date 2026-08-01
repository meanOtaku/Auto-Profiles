"""Profile-independent face-quality evaluation.

M4 rejects samples that are too small, blurry, poorly exposed, strongly
rotated, or geometrically inconsistent with an unoccluded face, and records a
reason for every rejection. Quality evaluation never touches profile,
candidate, or recognition state.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot
from typing import Protocol

import cv2
import numpy as np

from face_profile.camera import Frame
from face_profile.config import QualityConfig
from face_profile.vision.detection import BoundingBox, FaceDetection, FaceLandmarks


class QualityEvaluationError(RuntimeError):
    """Raised when quality evaluation cannot process a detection safely."""


@dataclass(frozen=True, slots=True)
class QualityResult:
    """Deterministic quality decision for one detection.

    ``reasons`` lists every failing check, not only the first one, so callers
    and audit logs can explain a rejection completely.
    """

    accepted: bool
    score: float
    reasons: tuple[str, ...]


class QualityEvaluator(Protocol):
    """Evaluate one detection's crop for enrollment/matching suitability."""

    def evaluate(self, frame: Frame, detection: FaceDetection) -> QualityResult: ...


class HeuristicQualityEvaluator:
    """Threshold-based quality evaluator using classical image heuristics.

    Occlusion is approximated with a landmark-geometry consistency proxy
    rather than a trained occlusion or segmentation model: no such model is
    part of the recommended stack at this milestone. The proxy flags eye and
    mouth landmarks that are not in a plausible unoccluded configuration. It
    is a documented limitation, not a spoof or occlusion detector; M14 owns
    trained anti-spoof and liveness evaluation.
    """

    def __init__(self, config: QualityConfig) -> None:
        self._config = config

    def evaluate(self, frame: Frame, detection: FaceDetection) -> QualityResult:
        """Score and accept or reject one detection against configured bounds."""

        config = self._config
        box = detection.bounding_box
        reasons: list[str] = []
        scores: list[float] = []

        size_score, size_reason = _evaluate_size(box, config)
        scores.append(size_score)
        if size_reason is not None:
            reasons.append(size_reason)

        crop = _extract_crop(frame, box)
        if crop is None or crop.size == 0:
            return QualityResult(accepted=False, score=0.0, reasons=("crop_unavailable",))
        gray = _to_grayscale(crop)

        sharpness_score, sharpness_reason = _evaluate_sharpness(gray, config)
        scores.append(sharpness_score)
        if sharpness_reason is not None:
            reasons.append(sharpness_reason)

        exposure_score, exposure_reasons = _evaluate_exposure(gray, config)
        scores.append(exposure_score)
        reasons.extend(exposure_reasons)

        pose_score, pose_reasons = _evaluate_pose(detection.landmarks, box, config)
        scores.append(pose_score)
        reasons.extend(pose_reasons)

        occlusion_score, occlusion_reason = _evaluate_occlusion_proxy(
            detection.landmarks, box, config
        )
        scores.append(occlusion_score)
        if occlusion_reason is not None:
            reasons.append(occlusion_reason)

        overall_score = sum(scores) / len(scores)
        return QualityResult(
            accepted=len(reasons) == 0,
            score=overall_score,
            reasons=tuple(reasons),
        )


def create_quality_evaluator(config: QualityConfig) -> QualityEvaluator | None:
    """Create the configured evaluator only when M4 is explicitly enabled."""

    if not config.enabled:
        return None
    return HeuristicQualityEvaluator(config)


def _extract_crop(frame: Frame, box: BoundingBox) -> np.ndarray | None:
    height, width = frame.image.shape[:2]
    left = max(0, int(box.x))
    top = max(0, int(box.y))
    right = min(width, int(box.x + box.width))
    bottom = min(height, int(box.y + box.height))
    if right <= left or bottom <= top:
        return None
    return frame.image[top:bottom, left:right]


def _to_grayscale(crop: np.ndarray) -> np.ndarray:
    if crop.ndim == 2:
        return crop
    try:
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    except cv2.error as error:
        raise QualityEvaluationError("grayscale conversion failed") from error


def _evaluate_size(box: BoundingBox, config: QualityConfig) -> tuple[float, str | None]:
    if box.width < config.min_face_width or box.height < config.min_face_height:
        return 0.0, "too_small"
    width_ratio = box.width / config.min_face_width
    height_ratio = box.height / config.min_face_height
    return min(1.0, min(width_ratio, height_ratio) / 2.0), None


def _evaluate_sharpness(gray: np.ndarray, config: QualityConfig) -> tuple[float, str | None]:
    try:
        variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except cv2.error as error:
        raise QualityEvaluationError("sharpness evaluation failed") from error
    if variance < config.min_sharpness_variance:
        return 0.0, "blurry"
    if config.min_sharpness_variance <= 0.0:
        return 1.0, None
    return min(1.0, variance / (config.min_sharpness_variance * 2.0)), None


def _evaluate_exposure(gray: np.ndarray, config: QualityConfig) -> tuple[float, tuple[str, ...]]:
    mean_brightness = float(gray.mean())
    overexposed_fraction = float(np.count_nonzero(gray >= 250) / gray.size)
    reasons: list[str] = []
    if mean_brightness < config.min_mean_brightness:
        reasons.append("too_dark")
    if mean_brightness > config.max_mean_brightness:
        reasons.append("overexposed")
    if overexposed_fraction > config.max_overexposed_fraction:
        reasons.append("overexposed")
    midpoint = (config.min_mean_brightness + config.max_mean_brightness) / 2.0
    half_range = max(1.0, (config.max_mean_brightness - config.min_mean_brightness) / 2.0)
    distance = abs(mean_brightness - midpoint) / half_range
    score = max(0.0, 1.0 - distance)
    return score, tuple(dict.fromkeys(reasons))


def _evaluate_pose(
    landmarks: FaceLandmarks, box: BoundingBox, config: QualityConfig
) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = []
    dx = landmarks.left_eye.x - landmarks.right_eye.x
    dy = landmarks.left_eye.y - landmarks.right_eye.y
    roll_degrees = abs(degrees(atan2(dy, dx)))
    if roll_degrees > config.max_roll_degrees:
        reasons.append("excessive_roll")
    roll_score = max(0.0, 1.0 - roll_degrees / max(config.max_roll_degrees, 1e-6))

    eye_midpoint_x = (landmarks.left_eye.x + landmarks.right_eye.x) / 2.0
    eye_distance = hypot(dx, dy)
    yaw_asymmetry = abs(landmarks.nose.x - eye_midpoint_x) / max(eye_distance, 1e-6)
    if yaw_asymmetry > config.max_yaw_asymmetry:
        reasons.append("excessive_yaw")
    yaw_score = max(0.0, 1.0 - yaw_asymmetry / max(config.max_yaw_asymmetry, 1e-6))

    return (roll_score + yaw_score) / 2.0, tuple(reasons)


def _evaluate_occlusion_proxy(
    landmarks: FaceLandmarks, box: BoundingBox, config: QualityConfig
) -> tuple[float, str | None]:
    eyes_y = (landmarks.left_eye.y + landmarks.right_eye.y) / 2.0
    mouth_y = (landmarks.left_mouth.y + landmarks.right_mouth.y) / 2.0
    ordering_consistent = eyes_y < landmarks.nose.y < mouth_y

    eye_distance = hypot(
        landmarks.left_eye.x - landmarks.right_eye.x,
        landmarks.left_eye.y - landmarks.right_eye.y,
    )
    reference = max(box.width, 1e-6)
    spread_ratio = eye_distance / reference
    spread_plausible = 0.2 <= spread_ratio <= 0.9

    margin = config.max_landmark_margin_violation * max(box.width, box.height)
    inside_box = all(
        box.x - margin <= point.x <= box.x + box.width + margin
        and box.y - margin <= point.y <= box.y + box.height + margin
        for point in (
            landmarks.left_eye,
            landmarks.right_eye,
            landmarks.nose,
            landmarks.left_mouth,
            landmarks.right_mouth,
        )
    )

    if ordering_consistent and spread_plausible and inside_box:
        return 1.0, None
    return 0.0, "occlusion_suspected"
