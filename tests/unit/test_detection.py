from datetime import UTC, datetime

import cv2
import numpy as np
import pytest

from face_profile.camera import Frame
from face_profile.vision.detection import FaceDetection


def _frame() -> Frame:
    return Frame(
        source_id="synthetic",
        sequence=0,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        image=np.zeros((64, 96, 3), dtype=np.uint8),
    )


def _detection(x: float, confidence: float) -> FaceDetection:
    from face_profile.vision.detection import (
        BoundingBox,
        FaceDetection,
        FaceLandmarks,
        Point,
    )

    return FaceDetection(
        bounding_box=BoundingBox(x=x, y=10.0, width=20.0, height=24.0),
        confidence=confidence,
        landmarks=FaceLandmarks(
            left_eye=Point(x=x + 6.0, y=18.0),
            right_eye=Point(x=x + 14.0, y=18.0),
            nose=Point(x=x + 10.0, y=23.0),
            left_mouth=Point(x=x + 7.0, y=28.0),
            right_mouth=Point(x=x + 13.0, y=28.0),
        ),
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_mock_detector_returns_zero_one_or_multiple_faces(count: int) -> None:
    from face_profile.vision.detection import MockFaceDetector

    detections = tuple(_detection(float(index * 24), 0.9 - index * 0.1) for index in range(count))
    detector = MockFaceDetector(detections)

    assert detector.detect(_frame()) == detections


def test_yunet_detector_normalizes_filters_and_orders_backend_rows() -> None:
    from face_profile.vision.detection import YuNetFaceDetector

    class Backend:
        def __init__(self) -> None:
            self.input_size: tuple[int, int] | None = None

        def setInputSize(self, input_size: tuple[int, int]) -> None:
            self.input_size = input_size

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            assert image.shape == (64, 96, 3)
            return 1, np.array(
                [
                    [40, 8, 20, 24, 46, 16, 54, 16, 50, 21, 47, 26, 53, 26, 0.75],
                    [4, 10, 20, 24, 10, 18, 18, 18, 14, 23, 11, 28, 17, 28, 0.95],
                    [70, 8, 18, 22, 75, 15, 82, 15, 78, 20, 76, 25, 81, 25, 0.49],
                ],
                dtype=np.float32,
            )

    backend = Backend()
    detector = YuNetFaceDetector(backend=backend, confidence_threshold=0.5)

    detections = detector.detect(_frame())

    assert backend.input_size == (96, 64)
    assert [d.confidence for d in detections] == pytest.approx([0.95, 0.75])
    assert [d.bounding_box.x for d in detections] == pytest.approx([4.0, 40.0])
    assert detections[0].landmarks.nose.x == pytest.approx(14.0)


def test_yunet_detector_maps_documented_five_point_landmark_order() -> None:
    from face_profile.vision.detection import Point, YuNetFaceDetector

    rows = np.array(
        [[0, 0, 50, 50, 11, 12, 21, 22, 31, 32, 41, 42, 51, 52, 0.9]],
        dtype=np.float32,
    )

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            return 1, rows

    detection = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5).detect(_frame())[0]

    assert detection.landmarks.right_eye == Point(11.0, 12.0)
    assert detection.landmarks.left_eye == Point(21.0, 22.0)
    assert detection.landmarks.nose == Point(31.0, 32.0)
    assert detection.landmarks.right_mouth == Point(41.0, 42.0)
    assert detection.landmarks.left_mouth == Point(51.0, 52.0)


@pytest.mark.parametrize(
    "rows",
    [
        np.zeros((1, 14), dtype=np.float32),
        np.array([[0, 0, -2, 10, 1, 1, 2, 1, 1, 2, 1, 3, 2, 3, 0.9]], dtype=np.float32),
        np.array([[0, 0, 10, 10, 1, 1, 2, 1, np.nan, 2, 1, 3, 2, 3, 0.9]]),
        np.array([[0, 0, 10, 10, 1, 1, 2, 1, 1, 2, 1, 3, 2, 3, 1.1]], dtype=np.float32),
    ],
)
def test_yunet_detector_rejects_malformed_backend_rows(rows: np.ndarray) -> None:
    from face_profile.vision.detection import DetectionError, YuNetFaceDetector

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            return 1, rows

    detector = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5)

    with pytest.raises(DetectionError, match="invalid detector output"):
        detector.detect(_frame())


def test_yunet_detector_wraps_backend_exceptions_without_details() -> None:
    from face_profile.vision.detection import DetectionError, YuNetFaceDetector

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            raise RuntimeError("private backend and model path details")

    detector = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5)

    with pytest.raises(DetectionError, match="detector backend failed") as captured:
        detector.detect(_frame())
    assert "private backend" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_yunet_detector_rejects_unsuccessful_native_status() -> None:
    from face_profile.vision.detection import DetectionError, YuNetFaceDetector

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, None]:
            return 0, None

    detector = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5)

    with pytest.raises(DetectionError, match="detector backend failed"):
        detector.detect(_frame())


def test_yunet_detector_rejects_complex_backend_rows() -> None:
    from face_profile.vision.detection import DetectionError, YuNetFaceDetector

    rows = np.zeros((1, 15), dtype=np.complex64)
    rows[0, 2:4] = 10
    rows[0, 14] = 0.9

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            return 1, rows

    detector = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5)

    with pytest.raises(DetectionError, match="invalid detector output"):
        detector.detect(_frame())


def test_yunet_detector_rejects_nonintersecting_low_confidence_rows() -> None:
    from face_profile.vision.detection import DetectionError, YuNetFaceDetector

    rows = np.array(
        [[200, 0, 10, 10, 201, 1, 202, 1, 201, 2, 201, 3, 202, 3, 0.1]],
        dtype=np.float32,
    )

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            return 1, rows

    detector = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5)

    with pytest.raises(DetectionError, match="invalid detector output"):
        detector.detect(_frame())


def test_yunet_detector_rejects_more_rows_than_its_configured_bound() -> None:
    from face_profile.vision.detection import DetectionError, YuNetFaceDetector

    row = np.array(
        [0, 0, 10, 10, 1, 1, 2, 1, 1, 2, 1, 3, 2, 3, 0.9],
        dtype=np.float32,
    )
    rows = np.tile(row, (5001, 1))

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            return 1, rows

    detector = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5)

    with pytest.raises(DetectionError, match="invalid detector output"):
        detector.detect(_frame())


def test_yunet_detector_wraps_output_conversion_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.vision.detection import DetectionError, YuNetFaceDetector

    class Backend:
        def setInputSize(self, input_size: tuple[int, int]) -> None:
            pass

        def detect(self, image: np.ndarray) -> tuple[int, np.ndarray]:
            return 1, np.zeros((1, 15), dtype=np.float32)

    def fail(value: object) -> np.ndarray:
        del value
        raise RuntimeError("private conversion details")

    monkeypatch.setattr(np, "asarray", fail)
    detector = YuNetFaceDetector(backend=Backend(), confidence_threshold=0.5)

    with pytest.raises(DetectionError, match="invalid detector output") as captured:
        detector.detect(_frame())
    assert "private conversion" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_render_detection_debug_annotates_a_copy() -> None:
    from face_profile.vision.detection import render_detection_debug

    frame = _frame()
    original = frame.image.copy()

    rendered = render_detection_debug(frame, (_detection(12.0, 0.9),))

    assert rendered.source_id == frame.source_id
    assert rendered.sequence == frame.sequence
    assert rendered.captured_at == frame.captured_at
    np.testing.assert_array_equal(frame.image, original)
    assert not np.array_equal(rendered.image, original)
    assert "image=" not in repr(rendered)


def test_render_detection_debug_wraps_backend_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.vision.detection import DetectionError, render_detection_debug

    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("private backend and model path details")

    monkeypatch.setattr(cv2, "rectangle", fail)

    with pytest.raises(DetectionError, match="debug rendering failed") as captured:
        render_detection_debug(_frame(), (_detection(12.0, 0.9),))
    assert "private backend" not in str(captured.value)
    assert captured.value.__cause__ is None
