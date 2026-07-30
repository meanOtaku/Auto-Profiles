"""Construct detector adapters from validated configuration."""

import hashlib
import hmac
from collections.abc import Callable
from threading import Lock
from typing import Any, cast

import cv2
import numpy as np

from face_profile.config import DetectionConfig
from face_profile.vision.detection import (
    FaceDetector,
    MockFaceDetector,
    YuNetBackend,
    YuNetFaceDetector,
)

BackendFactory = Callable[[bytes, float, float, int], YuNetBackend]
_OPENCV_LOGGING_LOCK = Lock()


class DetectionModelError(RuntimeError):
    """Raised when a detector model cannot be trusted or loaded safely."""


def create_face_detector(
    config: DetectionConfig,
    *,
    backend_factory: BackendFactory | None = None,
) -> FaceDetector | None:
    """Build the configured detector after model-integrity validation."""

    if not config.enabled:
        return None
    if config.backend == "mock":
        return MockFaceDetector()

    model_path = config.model_path
    expected_sha256 = config.model_sha256
    if model_path is None or expected_sha256 is None:
        raise DetectionModelError("detector model configuration is incomplete")
    try:
        model_bytes = model_path.read_bytes()
    except OSError:
        raise DetectionModelError("detector model unavailable") from None
    if not hmac.compare_digest(hashlib.sha256(model_bytes).hexdigest(), expected_sha256):
        raise DetectionModelError("detector model integrity check failed")
    factory = backend_factory if backend_factory is not None else _create_opencv_yunet_backend
    try:
        backend = factory(
            model_bytes,
            config.confidence_threshold,
            config.nms_threshold,
            config.top_k,
        )
    except Exception:
        raise DetectionModelError("detector model loading failed") from None
    return YuNetFaceDetector(
        backend=backend,
        confidence_threshold=config.confidence_threshold,
        max_detections=config.top_k,
    )


def _create_opencv_yunet_backend(
    model_bytes: bytes,
    confidence_threshold: float,
    nms_threshold: float,
    top_k: int,
) -> YuNetBackend:
    opencv_logging = cast(Any, cv2.utils.logging)
    with _OPENCV_LOGGING_LOCK:
        previous_log_level = opencv_logging.getLogLevel()
        opencv_logging.setLogLevel(opencv_logging.LOG_LEVEL_SILENT)
        try:
            return cast(
                YuNetBackend,
                cv2.FaceDetectorYN.create(
                    "onnx",
                    np.frombuffer(model_bytes, dtype=np.uint8),
                    np.empty(0, dtype=np.uint8),
                    (0, 0),
                    confidence_threshold,
                    nms_threshold,
                    top_k,
                ),
            )
        finally:
            opencv_logging.setLogLevel(previous_log_level)
