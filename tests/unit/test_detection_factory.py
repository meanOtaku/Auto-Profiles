import hashlib
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pytest


def test_detector_factory_preserves_disabled_safe_default() -> None:
    from face_profile.config import DetectionConfig
    from face_profile.vision.factory import create_face_detector

    assert create_face_detector(DetectionConfig()) is None


def test_detector_factory_creates_hardware_free_mock() -> None:
    from face_profile.config import DetectionConfig
    from face_profile.vision.detection import MockFaceDetector
    from face_profile.vision.factory import create_face_detector

    detector = create_face_detector(DetectionConfig(enabled=True))

    assert isinstance(detector, MockFaceDetector)


def test_detector_factory_rejects_model_checksum_mismatch_before_load(tmp_path: Path) -> None:
    from face_profile.config import DetectionConfig
    from face_profile.vision.factory import DetectionModelError, create_face_detector

    model = tmp_path / "yunet.onnx"
    model.write_bytes(b"not-the-approved-model")
    config = DetectionConfig(
        enabled=True,
        backend="yunet",
        model_path=model,
        model_sha256="0" * 64,
    )
    backend_loaded = False

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, None]:
            return 1, None

    def backend_factory(
        model_bytes: bytes,
        confidence_threshold: float,
        nms_threshold: float,
        top_k: int,
    ) -> Backend:
        del model_bytes, confidence_threshold, nms_threshold, top_k
        nonlocal backend_loaded
        backend_loaded = True
        raise AssertionError("unverified model reached backend")

    with pytest.raises(DetectionModelError, match="model integrity check failed") as captured:
        create_face_detector(config, backend_factory=backend_factory)

    assert backend_loaded is False
    assert str(model) not in str(captured.value)


def test_detector_factory_suppresses_backend_exception_details(tmp_path: Path) -> None:
    from face_profile.config import DetectionConfig
    from face_profile.vision.factory import DetectionModelError, create_face_detector

    approved = b"approved-model"
    model = tmp_path / "yunet.onnx"
    model.write_bytes(approved)
    config = DetectionConfig(
        enabled=True,
        backend="yunet",
        model_path=model,
        model_sha256=hashlib.sha256(approved).hexdigest(),
    )

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, None]:
            return 1, None

    def backend_factory(
        model_bytes: bytes,
        confidence_threshold: float,
        nms_threshold: float,
        top_k: int,
    ) -> Backend:
        del model_bytes, confidence_threshold, nms_threshold, top_k
        raise RuntimeError("PRIVATE model path /secret/model.onnx")

    with pytest.raises(DetectionModelError, match="detector model loading failed") as captured:
        create_face_detector(config, backend_factory=backend_factory)

    assert "PRIVATE" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_detector_factory_loads_verified_yunet_backend(tmp_path: Path) -> None:
    from face_profile.config import DetectionConfig
    from face_profile.vision.detection import YuNetFaceDetector
    from face_profile.vision.factory import create_face_detector

    model = tmp_path / "yunet.onnx"
    model.write_bytes(b"approved-model")
    expected_sha256 = hashlib.sha256(model.read_bytes()).hexdigest()
    config = DetectionConfig(
        enabled=True,
        backend="yunet",
        model_path=model,
        model_sha256=expected_sha256,
        confidence_threshold=0.6,
        nms_threshold=0.2,
        top_k=250,
    )
    received: tuple[bytes, float, float, int] | None = None

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, None]:
            return 1, None

    def backend_factory(
        model_bytes: bytes,
        confidence_threshold: float,
        nms_threshold: float,
        top_k: int,
    ) -> Backend:
        nonlocal received
        received = (model_bytes, confidence_threshold, nms_threshold, top_k)
        return Backend()

    detector = create_face_detector(config, backend_factory=backend_factory)

    assert isinstance(detector, YuNetFaceDetector)
    assert received == (b"approved-model", 0.6, 0.2, 250)


def test_detector_factory_loads_the_exact_bytes_that_were_verified(tmp_path: Path) -> None:
    from face_profile.config import DetectionConfig
    from face_profile.vision.factory import create_face_detector

    approved = b"approved-model"
    model = tmp_path / "yunet.onnx"
    model.write_bytes(approved)
    config = DetectionConfig(
        enabled=True,
        backend="yunet",
        model_path=model,
        model_sha256=hashlib.sha256(approved).hexdigest(),
    )
    loaded_bytes: bytes | None = None

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, None]:
            return 1, None

    def backend_factory(
        model_bytes: bytes,
        confidence_threshold: float,
        nms_threshold: float,
        top_k: int,
    ) -> Backend:
        del confidence_threshold, nms_threshold, top_k
        nonlocal loaded_bytes
        model.write_bytes(b"tampered-after-verification")
        loaded_bytes = model_bytes
        return Backend()

    create_face_detector(config, backend_factory=backend_factory)

    assert loaded_bytes == approved


def test_opencv_backend_creation_silences_and_restores_native_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.vision.factory import _create_opencv_yunet_backend

    levels: list[int] = []
    opencv_logging = cast(Any, cv2.utils.logging)
    monkeypatch.setattr(opencv_logging, "getLogLevel", lambda: 3)
    monkeypatch.setattr(opencv_logging, "setLogLevel", levels.append)

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, None]:
            return 1, None

    monkeypatch.setattr(
        cv2.FaceDetectorYN,
        "create",
        lambda *args, **kwargs: Backend(),
    )

    backend = _create_opencv_yunet_backend(b"model", 0.5, 0.3, 5000)

    assert isinstance(backend, Backend)
    assert levels == [opencv_logging.LOG_LEVEL_SILENT, 3]
