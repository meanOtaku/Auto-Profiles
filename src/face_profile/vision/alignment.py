"""Similarity-transform face alignment to a fixed embedding-model template.

M4 aligns only crops that already passed quality evaluation. Alignment output
feeds the M5 embedding model; it is not itself an identity or matching
decision and retains no profile or track association.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeAlias, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from face_profile.camera import Frame
from face_profile.config import QualityConfig
from face_profile.vision.detection import FaceDetection

AlignedImage: TypeAlias = NDArray[np.uint8]


class AlignmentError(RuntimeError):
    """Raised when a face crop cannot be aligned to the reference template."""


# The published InsightFace/ArcFace five-point 112x112 alignment reference
# template (right eye, left eye, nose tip, right mouth corner, left mouth
# corner). This is a fixed geometric calibration matching the embedding
# model's expected input convention, not a configurable recognition
# threshold, so it is not exposed through application configuration.
_REFERENCE_LANDMARKS_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)
_REFERENCE_SIZE = 112


@dataclass(frozen=True, slots=True)
class AlignedFace:
    """A canonically aligned face crop ready for embedding generation."""

    image: AlignedImage = field(repr=False)
    output_width: int
    output_height: int


class FaceAligner(Protocol):
    """Warp a detection's crop to a canonical, fixed-size template."""

    def align(self, frame: Frame, detection: FaceDetection) -> AlignedFace: ...


class SimilarityFaceAligner:
    """Align faces using a similarity transform to the ArcFace 5-point template."""

    def __init__(self, *, output_width: int, output_height: int) -> None:
        if output_width < 1 or output_height < 1:
            raise ValueError("output dimensions must be positive")
        self._output_width = output_width
        self._output_height = output_height
        scale_x = output_width / _REFERENCE_SIZE
        scale_y = output_height / _REFERENCE_SIZE
        self._reference = _REFERENCE_LANDMARKS_112 * np.array([scale_x, scale_y], dtype=np.float32)

    def align(self, frame: Frame, detection: FaceDetection) -> AlignedFace:
        """Estimate a similarity transform from landmarks and warp the frame."""

        landmarks = detection.landmarks
        source = np.array(
            [
                [landmarks.right_eye.x, landmarks.right_eye.y],
                [landmarks.left_eye.x, landmarks.left_eye.y],
                [landmarks.nose.x, landmarks.nose.y],
                [landmarks.right_mouth.x, landmarks.right_mouth.y],
                [landmarks.left_mouth.x, landmarks.left_mouth.y],
            ],
            dtype=np.float32,
        )
        try:
            transform, _ = cv2.estimateAffinePartial2D(source, self._reference, method=cv2.LMEDS)
        except cv2.error as error:
            raise AlignmentError("alignment transform estimation failed") from error
        if transform is None:
            raise AlignmentError("alignment transform could not be estimated")
        try:
            warped = cv2.warpAffine(
                frame.image,
                transform,
                (self._output_width, self._output_height),
                borderMode=cv2.BORDER_REPLICATE,
            )
        except cv2.error as error:
            raise AlignmentError("alignment warp failed") from error
        if warped.dtype != np.uint8:
            raise AlignmentError("aligned output has an unexpected dtype")
        return AlignedFace(
            image=cast(AlignedImage, warped),
            output_width=self._output_width,
            output_height=self._output_height,
        )


def create_aligner(config: QualityConfig) -> FaceAligner | None:
    """Create the configured aligner only when M4 is explicitly enabled."""

    if not config.enabled:
        return None
    return SimilarityFaceAligner(
        output_width=config.aligned_output_width,
        output_height=config.aligned_output_height,
    )
