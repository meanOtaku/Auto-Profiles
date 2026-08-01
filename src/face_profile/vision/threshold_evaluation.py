"""Similarity threshold evaluation over locally supplied labeled pairs.

M5 delivers the evaluation primitives needed to select an initial similarity
threshold and margin from an approved, privacy-safe, locally labeled pair
dataset, and to report false accepts/rejects for that dataset. This module
never fabricates evaluation evidence: it computes statistics only from pairs
the caller explicitly supplies. It does not decide identity for live tracks;
that decision state machine belongs to M7.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from math import sqrt


class ThresholdEvaluationError(ValueError):
    """Raised when a labeled pair dataset cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class PairScore:
    """One labeled similarity observation from an approved evaluation set."""

    similarity: float
    is_genuine: bool
    cohort: str | None = None

    def __post_init__(self) -> None:
        if not -1.0 <= self.similarity <= 1.0:
            raise ThresholdEvaluationError("similarity must be between -1.0 and 1.0")


@dataclass(frozen=True, slots=True)
class ThresholdMetrics:
    """False-accept/false-reject rates for one candidate threshold."""

    threshold: float
    false_accept_rate: float
    false_reject_rate: float
    false_accept_count: int
    false_reject_count: int


@dataclass(frozen=True, slots=True)
class ScoreStatistics:
    """Summary statistics with a normal-approximation 95% confidence interval."""

    count: int
    mean: float
    standard_deviation: float
    minimum: float
    maximum: float
    confidence_interval_95: tuple[float, float]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Full threshold-sweep evaluation result for one labeled pair dataset."""

    thresholds: tuple[ThresholdMetrics, ...]
    genuine_statistics: ScoreStatistics
    impostor_statistics: ScoreStatistics
    equal_error_rate: float
    equal_error_threshold: float
    cohorts: tuple[str, ...]


def evaluate_thresholds(
    pairs: Sequence[PairScore], thresholds: Sequence[float]
) -> EvaluationReport:
    """Compute FAR/FRR per threshold, score statistics, and an EER estimate.

    Raises when the dataset lacks at least one genuine and one impostor
    pair, or when no thresholds are supplied, because false-accept and
    false-reject rates are undefined without both classes.
    """

    if len(pairs) == 0:
        raise ThresholdEvaluationError("at least one labeled pair is required")
    if len(thresholds) == 0:
        raise ThresholdEvaluationError("at least one threshold is required")

    genuine = [pair.similarity for pair in pairs if pair.is_genuine]
    impostor = [pair.similarity for pair in pairs if not pair.is_genuine]
    if len(genuine) == 0 or len(impostor) == 0:
        raise ThresholdEvaluationError(
            "evaluation requires at least one genuine and one impostor pair"
        )

    sorted_thresholds = tuple(sorted(thresholds))
    metrics = tuple(_evaluate_one_threshold(t, genuine, impostor) for t in sorted_thresholds)
    equal_error_rate, equal_error_threshold = _estimate_equal_error(metrics)
    cohorts = tuple(sorted({pair.cohort for pair in pairs if pair.cohort is not None}))

    return EvaluationReport(
        thresholds=metrics,
        genuine_statistics=_statistics(genuine),
        impostor_statistics=_statistics(impostor),
        equal_error_rate=equal_error_rate,
        equal_error_threshold=equal_error_threshold,
        cohorts=cohorts,
    )


def _evaluate_one_threshold(
    threshold: float, genuine: Sequence[float], impostor: Sequence[float]
) -> ThresholdMetrics:
    false_accepts = sum(1 for score in impostor if score >= threshold)
    false_rejects = sum(1 for score in genuine if score < threshold)
    return ThresholdMetrics(
        threshold=threshold,
        false_accept_rate=false_accepts / len(impostor),
        false_reject_rate=false_rejects / len(genuine),
        false_accept_count=false_accepts,
        false_reject_count=false_rejects,
    )


def _estimate_equal_error(
    metrics: Sequence[ThresholdMetrics],
) -> tuple[float, float]:
    """Estimate the FAR/FRR crossing point.

    Linearly interpolates between adjacent swept thresholds when the sweep
    brackets a sign change in (FAR - FRR). When the sweep never crosses zero
    (for example a coarse or one-sided sweep), the closest single threshold
    is reported instead; callers should widen the sweep for a precise EER.
    """

    for previous, current in pairwise(metrics):
        previous_diff = previous.false_accept_rate - previous.false_reject_rate
        current_diff = current.false_accept_rate - current.false_reject_rate
        if previous_diff == 0.0:
            return previous.false_accept_rate, previous.threshold
        if (previous_diff > 0.0) != (current_diff > 0.0):
            span = current_diff - previous_diff
            weight = -previous_diff / span if span != 0.0 else 0.0
            threshold = previous.threshold + weight * (current.threshold - previous.threshold)
            far = previous.false_accept_rate + weight * (
                current.false_accept_rate - previous.false_accept_rate
            )
            frr = previous.false_reject_rate + weight * (
                current.false_reject_rate - previous.false_reject_rate
            )
            return (far + frr) / 2.0, threshold
    closest = min(metrics, key=lambda m: abs(m.false_accept_rate - m.false_reject_rate))
    return (closest.false_accept_rate + closest.false_reject_rate) / 2.0, closest.threshold


def _statistics(scores: Sequence[float]) -> ScoreStatistics:
    count = len(scores)
    mean = sum(scores) / count
    variance = sum((score - mean) ** 2 for score in scores) / count
    standard_deviation = sqrt(variance)
    margin = 1.96 * standard_deviation / sqrt(count) if count > 1 else 0.0
    return ScoreStatistics(
        count=count,
        mean=mean,
        standard_deviation=standard_deviation,
        minimum=min(scores),
        maximum=max(scores),
        confidence_interval_95=(mean - margin, mean + margin),
    )
