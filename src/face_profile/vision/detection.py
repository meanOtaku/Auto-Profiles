"""Profile-independent face-detection contracts and adapters."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from face_profile.camera import Frame


class DetectionError(RuntimeError):
    """Raised when detector inference cannot produce trusted detections."""


@dataclass(frozen=True)
class Point:
    """A two-dimensional image coordinate."""

    x: float
    y: float


@dataclass(frozen=True)
class BoundingBox:
    """An axis-aligned face bounding box in image coordinates."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class FaceLandmarks:
    """Five-point facial landmarks emitted by the detector."""

    left_eye: Point
    right_eye: Point
    nose: Point
    left_mouth: Point
    right_mouth: Point


@dataclass(frozen=True)
class FaceDetection:
    """One profile-independent face observation."""

    bounding_box: BoundingBox
    confidence: float
    landmarks: FaceLandmarks


class FaceDetector(Protocol):
    """Detect every face present in one frame."""

    def detect(self, frame: Frame) -> tuple[FaceDetection, ...]: ...


class YuNetBackend(Protocol):
    """Narrow protocol for OpenCV-compatible YuNet detector backends."""

    def setInputSize(self, input_size: tuple[int, int]) -> None: ...

    def detect(self, image: NDArray[np.uint8]) -> tuple[int, NDArray[Any] | None]: ...


class YuNetFaceDetector:
    """Normalize YuNet backend rows into profile-independent detections."""

    def __init__(
        self,
        *,
        backend: YuNetBackend,
        confidence_threshold: float,
        max_detections: int = 5000,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence threshold must be between zero and one")
        if max_detections < 1:
            raise ValueError("maximum detections must be positive")
        self._backend = backend
        self._confidence_threshold = confidence_threshold
        self._max_detections = max_detections

    def detect(self, frame: Frame) -> tuple[FaceDetection, ...]:
        """Detect, normalize, filter, and deterministically order faces."""

        height, width = frame.image.shape[:2]
        try:
            self._backend.setInputSize((width, height))
            status, rows = self._backend.detect(frame.image)
        except Exception:
            raise DetectionError("detector backend failed") from None
        if status != 1:
            raise DetectionError("detector backend failed")
        if rows is None:
            return ()

        try:
            normalized = np.asarray(rows)
        except Exception:
            raise DetectionError("invalid detector output") from None
        if normalized.ndim != 2 or normalized.shape[1] != 15:
            raise DetectionError("invalid detector output")
        if normalized.shape[0] > self._max_detections:
            raise DetectionError("invalid detector output")
        try:
            is_numeric = np.issubdtype(normalized.dtype, np.integer) or np.issubdtype(
                normalized.dtype,
                np.floating,
            )
            is_finite = bool(np.isfinite(normalized).all()) if is_numeric else False
        except Exception:
            raise DetectionError("invalid detector output") from None
        if not is_numeric or not is_finite:
            raise DetectionError("invalid detector output")

        detections: list[FaceDetection] = []
        for row in normalized:
            confidence = float(row[14])
            x, y, box_width, box_height = (float(value) for value in row[:4])
            if not 0.0 <= confidence <= 1.0 or box_width <= 0.0 or box_height <= 0.0:
                raise DetectionError("invalid detector output")
            left = max(0.0, x)
            top = max(0.0, y)
            right = min(float(width), x + box_width)
            bottom = min(float(height), y + box_height)
            if right <= left or bottom <= top:
                raise DetectionError("invalid detector output")
            if confidence < self._confidence_threshold:
                continue
            detections.append(
                FaceDetection(
                    bounding_box=BoundingBox(
                        x=left,
                        y=top,
                        width=right - left,
                        height=bottom - top,
                    ),
                    confidence=confidence,
                    landmarks=FaceLandmarks(
                        right_eye=Point(float(row[4]), float(row[5])),
                        left_eye=Point(float(row[6]), float(row[7])),
                        nose=Point(float(row[8]), float(row[9])),
                        right_mouth=Point(float(row[10]), float(row[11])),
                        left_mouth=Point(float(row[12]), float(row[13])),
                    ),
                )
            )
        return tuple(
            sorted(
                detections,
                key=lambda detection: (
                    -detection.confidence,
                    detection.bounding_box.y,
                    detection.bounding_box.x,
                ),
            )
        )


class MockFaceDetector:
    """Deterministic detector used by tests and hardware-free operation."""

    def __init__(self, detections: Iterable[FaceDetection] = ()) -> None:
        self._detections = tuple(detections)

    def detect(self, frame: Frame) -> tuple[FaceDetection, ...]:
        """Return configured detections without inspecting frame pixels."""

        return self._detections


def render_detection_debug(
    frame: Frame,
    detections: Iterable[FaceDetection],
) -> Frame:
    """Return a copied frame annotated with boxes, confidence, and landmarks."""

    image = frame.image.copy()
    try:
        for detection in detections:
            box = detection.bounding_box
            top_left = (round(box.x), round(box.y))
            bottom_right = (round(box.x + box.width), round(box.y + box.height))
            cv2.rectangle(image, top_left, bottom_right, (0, 255, 0), 1)
            cv2.putText(
                image,
                f"{detection.confidence:.2f}",
                (top_left[0], max(0, top_left[1] - 3)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )
            landmarks = detection.landmarks
            for point in (
                landmarks.left_eye,
                landmarks.right_eye,
                landmarks.nose,
                landmarks.left_mouth,
                landmarks.right_mouth,
            ):
                cv2.circle(image, (round(point.x), round(point.y)), 1, (0, 0, 255), -1)
    except Exception:
        raise DetectionError("debug rendering failed") from None
    return Frame(
        source_id=frame.source_id,
        sequence=frame.sequence,
        captured_at=frame.captured_at,
        image=image,
    )
