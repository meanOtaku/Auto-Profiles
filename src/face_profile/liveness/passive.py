"""Passive liveness heuristics over one aligned face crop.

M14 builds on M8's minimum temporal spoof/replay checks (internal
consistency and embedding variability across a candidate's own samples)
with a per-sample spatial heuristic. This is a classical, unevaluated
signal-processing proxy — spectral high-frequency energy ratio and a
specular-highlight-spread proxy — not a trained anti-spoof model. No
labeled printed-photo/replay/live dataset is available in this
environment to evaluate real-world accuracy; that evaluation is an
explicit, honestly recorded follow-up (see docs/reports/M14.md), not
claimed here. A trained model remains a documented future upgrade behind
the same :class:`PassiveLivenessEvaluator` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from face_profile.config import LivenessConfig
from face_profile.vision.alignment import AlignedFace


class LivenessEvaluationError(RuntimeError):
    """Raised when a liveness heuristic cannot process a crop safely."""


@dataclass(frozen=True, slots=True)
class LivenessResult:
    """Whether one sample passed the configured passive liveness checks."""

    passed: bool
    score: float
    reasons: tuple[str, ...]


class PassiveLivenessEvaluator(Protocol):
    """Evaluate one aligned crop for passive liveness signals."""

    def evaluate(self, aligned: AlignedFace) -> LivenessResult: ...


class HeuristicPassiveLivenessEvaluator:
    """Spectral-energy and specular-highlight heuristic passive evaluator."""

    def __init__(self, config: LivenessConfig) -> None:
        self._config = config

    def evaluate(self, aligned: AlignedFace) -> LivenessResult:
        """Score and accept or reject one crop against configured bounds."""

        gray = _to_grayscale(aligned.image)
        reasons: list[str] = []
        scores: list[float] = []

        frequency_ratio = _high_frequency_energy_ratio(gray)
        frequency_score, frequency_reason = _evaluate_band(
            frequency_ratio,
            minimum=self._config.high_frequency_energy_min,
            maximum=self._config.high_frequency_energy_max,
            reason="atypical_spectral_energy",
        )
        scores.append(frequency_score)
        if frequency_reason is not None:
            reasons.append(frequency_reason)

        specular_variance = _specular_highlight_spread(gray)
        specular_score = min(
            1.0, specular_variance / max(self._config.minimum_specular_variance, 1e-6)
        )
        scores.append(specular_score)
        if specular_variance < self._config.minimum_specular_variance:
            reasons.append("insufficient_specular_variation")

        overall_score = sum(scores) / len(scores)
        passed = overall_score >= self._config.minimum_passive_score and not reasons
        return LivenessResult(passed=passed, score=overall_score, reasons=tuple(reasons))


def create_passive_liveness_evaluator(config: LivenessConfig) -> PassiveLivenessEvaluator | None:
    """Create the configured evaluator only when M14 is explicitly enabled."""

    if not config.enabled:
        return None
    return HeuristicPassiveLivenessEvaluator(config)


def _to_grayscale(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    try:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    except cv2.error as error:
        raise LivenessEvaluationError("grayscale conversion failed") from error


def _high_frequency_energy_ratio(gray: np.ndarray) -> float:
    try:
        spectrum = np.fft.fftshift(np.fft.fft2(gray.astype(np.float64)))
    except Exception as error:
        raise LivenessEvaluationError("spectral analysis failed") from error
    magnitude = np.abs(spectrum)
    height, width = magnitude.shape
    centre_y, centre_x = height // 2, width // 2
    radius = min(height, width) // 4
    y_indices, x_indices = np.ogrid[:height, :width]
    low_frequency_mask = (x_indices - centre_x) ** 2 + (y_indices - centre_y) ** 2 <= radius**2
    total_energy = float(magnitude.sum())
    if total_energy <= 0.0:
        return 0.0
    low_energy = float(magnitude[low_frequency_mask].sum())
    return (total_energy - low_energy) / total_energy


def _specular_highlight_spread(gray: np.ndarray) -> float:
    threshold = float(np.percentile(gray, 95))
    bright_mask = gray >= threshold
    if not bright_mask.any():
        return 0.0
    y_positions, x_positions = np.nonzero(bright_mask)
    return float(np.var(x_positions) + np.var(y_positions))


def _evaluate_band(
    value: float, *, minimum: float, maximum: float, reason: str
) -> tuple[float, str | None]:
    if value < minimum or value > maximum:
        return 0.0, reason
    midpoint = (minimum + maximum) / 2.0
    half_range = max(1e-6, (maximum - minimum) / 2.0)
    distance = abs(value - midpoint) / half_range
    return max(0.0, 1.0 - distance), None
