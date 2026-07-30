import hashlib
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

CAMERA_FIXTURES = Path(__file__).parents[1] / "fixtures" / "camera"


def _assert_status(actual: object, expected: object) -> None:
    assert actual == expected


def test_camera_fixture_checksums() -> None:
    expected = {
        "synthetic.png": "0be35ea4c760625c8791ef729b0c55e68111fec4720830291f5f8939ce1c5738",
        "synthetic.avi": "19eb51c2c5c1afa278eb491fdbe3150148b7d452a0ac00f1b9ef368d14745072",
    }

    for name, digest in expected.items():
        with (CAMERA_FIXTURES / name).open("rb") as fixture:
            assert hashlib.file_digest(fixture, "sha256").hexdigest() == digest


def test_image_source_reads_once_then_terminates() -> None:
    from face_profile.camera import CameraStatus, EndOfFrames, ImageFrameSource

    image_path = CAMERA_FIXTURES / "synthetic.png"
    expected = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    assert expected is not None
    captured_at = datetime(2026, 1, 2, tzinfo=UTC)
    source = ImageFrameSource(image_path, source_id="fixture", clock=lambda: captured_at)

    _assert_status(source.health.status, CameraStatus.CLOSED)
    source.open()
    _assert_status(source.health.status, CameraStatus.HEALTHY)
    frame = source.read()

    assert frame.source_id == "fixture"
    assert frame.sequence == 0
    assert frame.captured_at == captured_at
    np.testing.assert_array_equal(frame.image, expected)
    with pytest.raises(EndOfFrames):
        source.read()
    _assert_status(source.health.status, CameraStatus.EXHAUSTED)
    assert source.health.frames_read == 1
    source.close()
    _assert_status(source.health.status, CameraStatus.CLOSED)


def test_image_source_reports_invalid_frame_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from face_profile.camera import CameraStatus, FrameSourceError, ImageFrameSource

    monkeypatch.setattr(
        cv2,
        "imread",
        lambda _path, _mode: np.zeros((2, 2, 3), dtype=np.float32),
    )
    source = ImageFrameSource(CAMERA_FIXTURES / "synthetic.png")

    with pytest.raises(FrameSourceError, match="invalid image"):
        source.open()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "invalid_frame"


def test_image_source_wraps_backend_decode_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.camera import CameraStatus, FrameSourceError, ImageFrameSource

    image_path = tmp_path / "synthetic.png"
    image_path.write_bytes(b"synthetic")

    def fail_decode(_path: str, _mode: int) -> object:
        raise cv2.error("backend details must not escape")

    monkeypatch.setattr(cv2, "imread", fail_decode)
    source = ImageFrameSource(image_path)

    with pytest.raises(FrameSourceError, match="image backend decode failed"):
        source.open()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "backend_decode_error"


def test_image_source_missing_file_does_not_leak_path_to_stderr(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from face_profile.camera import CameraStatus, FrameSourceError, ImageFrameSource

    missing = tmp_path / "private-camera-location.png"
    source = ImageFrameSource(missing)

    with pytest.raises(FrameSourceError, match="unable to open image source"):
        source.open()

    assert str(missing) not in capfd.readouterr().err
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "open_failed"


def test_video_source_reads_frames_then_terminates() -> None:
    from face_profile.camera import CameraStatus, EndOfFrames, VideoFrameSource

    video_path = CAMERA_FIXTURES / "synthetic.avi"
    times = iter(
        [
            datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC),
        ]
    )
    source = VideoFrameSource(video_path, source_id="video-fixture", clock=lambda: next(times))

    source.open()
    first = source.read()
    second = source.read()

    assert (first.sequence, second.sequence) == (0, 1)
    assert first.source_id == second.source_id == "video-fixture"
    assert int(first.image.max()) <= 5
    assert int(second.image.min()) >= 250
    with pytest.raises(EndOfFrames):
        source.read()
    _assert_status(source.health.status, CameraStatus.EXHAUSTED)
    assert source.health.frames_read == 2
    source.close()
    _assert_status(source.health.status, CameraStatus.CLOSED)


def test_video_source_reports_failure_before_expected_termination() -> None:
    from face_profile.camera import CameraStatus, FrameSourceError, VideoFrameSource

    class FailingVideoCapture:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, np.ndarray | None]:
            return False, None

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            if property_id == cv2.CAP_PROP_FRAME_COUNT:
                return 3.0
            if property_id == cv2.CAP_PROP_POS_FRAMES:
                return 1.0
            return 0.0

    source = VideoFrameSource(
        Path("synthetic.avi"),
        capture_factory=lambda _path: FailingVideoCapture(),
    )
    source.open()

    with pytest.raises(FrameSourceError, match="video frame unavailable"):
        source.read()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "read_failed"
    source.close()


def test_video_source_rejects_zero_frame_unknown_termination() -> None:
    from face_profile.camera import CameraStatus, FrameSourceError, VideoFrameSource

    class UnknownCountCapture:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, object]:
            return False, None

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            return 0.0

    source = VideoFrameSource(
        Path("synthetic.avi"),
        capture_factory=lambda _path: UnknownCountCapture(),
    )
    source.open()

    with pytest.raises(FrameSourceError, match="before expected termination"):
        source.read()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "read_failed"
    source.close()


def test_video_source_reports_invalid_frame_payload() -> None:
    from face_profile.camera import CameraStatus, FrameSourceError, VideoFrameSource

    class InvalidFrameCapture:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, object]:
            return True, np.zeros((2, 2, 3), dtype=np.float32)

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            return 1.0

    capture = InvalidFrameCapture()
    source = VideoFrameSource(
        Path("synthetic.avi"),
        capture_factory=lambda _path: capture,
    )
    source.open()

    with pytest.raises(FrameSourceError, match="invalid image"):
        source.read()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "invalid_frame"
    source.close()


def test_video_source_open_failure_does_not_expose_path() -> None:
    from face_profile.camera import FrameSourceError, VideoFrameSource

    class ClosedCapture:
        def isOpened(self) -> bool:
            return False

        def read(self) -> tuple[bool, object]:
            return False, None

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            return 0.0

    path = Path("private-camera-location.avi")
    source = VideoFrameSource(path, capture_factory=lambda _path: ClosedCapture())

    with pytest.raises(FrameSourceError) as captured:
        source.open()
    assert str(path) not in str(captured.value)


def test_video_source_wraps_backend_open_exceptions() -> None:
    from face_profile.camera import (
        CameraStatus,
        CaptureDevice,
        FrameSourceError,
        VideoFrameSource,
    )

    def capture_factory(_path: str) -> CaptureDevice:
        raise RuntimeError("backend details must not escape")

    source = VideoFrameSource(
        Path("synthetic.avi"),
        capture_factory=capture_factory,
    )

    with pytest.raises(FrameSourceError, match="video backend open failed"):
        source.open()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "backend_open_error"


def test_video_source_wraps_backend_read_exceptions() -> None:
    from face_profile.camera import CameraStatus, FrameSourceError, VideoFrameSource

    class ExplodingVideoCapture:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, object]:
            raise RuntimeError("backend details must not escape")

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            return 0.0

    source = VideoFrameSource(
        Path("synthetic.avi"),
        capture_factory=lambda _path: ExplodingVideoCapture(),
    )
    source.open()

    with pytest.raises(FrameSourceError, match="video backend read failed"):
        source.read()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "backend_read_error"
    source.close()


def test_webcam_source_wraps_backend_open_exceptions() -> None:
    from face_profile.camera import (
        CameraStatus,
        CaptureDevice,
        FrameSourceError,
        WebcamFrameSource,
    )

    def capture_factory(_device_index: int) -> CaptureDevice:
        raise RuntimeError("backend details must not escape")

    source = WebcamFrameSource(0, capture_factory=capture_factory)

    with pytest.raises(FrameSourceError, match="webcam backend open failed"):
        source.open()
    _assert_status(source.health.status, CameraStatus.FAILED)
    assert source.health.last_error_code == "backend_open_error"


def test_webcam_source_reconnects_after_temporary_read_failure() -> None:
    from face_profile.camera import CameraStatus, WebcamFrameSource

    image = np.full((2, 3, 3), 42, dtype=np.uint8)

    class FakeCapture:
        def __init__(self, result: tuple[bool, np.ndarray | None]) -> None:
            self._result = result
            self.released = False

        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, np.ndarray | None]:
            return self._result

        def release(self) -> None:
            self.released = True

        def get(self, _property_id: int) -> float:
            return 0.0

    captures = [FakeCapture((False, None)), FakeCapture((True, image))]
    created: list[FakeCapture] = []

    def create_capture(_device_index: int) -> FakeCapture:
        capture = captures[len(created)]
        created.append(capture)
        return capture

    captured_at = datetime(2026, 1, 3, tzinfo=UTC)
    source = WebcamFrameSource(
        0,
        retry_attempts=1,
        capture_factory=create_capture,
        clock=lambda: captured_at,
    )

    source.open()
    frame = source.read()

    assert len(created) == 2
    assert created[0].released is True
    assert frame.sequence == 0
    assert frame.captured_at == captured_at
    np.testing.assert_array_equal(frame.image, image)
    _assert_status(source.health.status, CameraStatus.HEALTHY)
    assert source.health.frames_read == 1
    assert source.health.temporary_failures == 1
    assert source.health.last_error_code == "temporary_read_failure"
    source.close()
    assert created[1].released is True
    _assert_status(source.health.status, CameraStatus.CLOSED)


def test_webcam_source_recovers_from_backend_read_exception() -> None:
    from face_profile.camera import CameraStatus, WebcamFrameSource

    image = np.full((2, 2, 3), 7, dtype=np.uint8)

    class Capture:
        def __init__(self, *, explode: bool) -> None:
            self._explode = explode

        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, object]:
            if self._explode:
                raise RuntimeError("backend details must not escape")
            return True, image

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            return 0.0

    captures = iter([Capture(explode=True), Capture(explode=False)])
    source = WebcamFrameSource(
        0,
        retry_attempts=1,
        capture_factory=lambda _device_index: next(captures),
    )
    source.open()

    frame = source.read()

    np.testing.assert_array_equal(frame.image, image)
    _assert_status(source.health.status, CameraStatus.HEALTHY)
    assert source.health.temporary_failures == 1
    assert source.health.last_error_code == "backend_read_error"
    source.close()


def test_webcam_source_retries_invalid_frame_payload() -> None:
    from face_profile.camera import CameraStatus, WebcamFrameSource

    valid_image = np.zeros((2, 2, 3), dtype=np.uint8)

    class Capture:
        def __init__(self, image: np.ndarray) -> None:
            self._image = image

        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, object]:
            return True, self._image

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            return 0.0

    captures = iter(
        [
            Capture(np.zeros((2, 2, 3), dtype=np.float32)),
            Capture(valid_image),
        ]
    )
    source = WebcamFrameSource(
        0,
        retry_attempts=1,
        capture_factory=lambda _device_index: next(captures),
    )
    source.open()

    frame = source.read()

    np.testing.assert_array_equal(frame.image, valid_image)
    _assert_status(source.health.status, CameraStatus.HEALTHY)
    assert source.health.temporary_failures == 1
    assert source.health.last_error_code == "invalid_frame"
    source.close()


def test_webcam_source_bounds_retries_and_reports_degraded_health() -> None:
    from face_profile.camera import (
        CameraStatus,
        TemporaryFrameSourceError,
        WebcamFrameSource,
    )

    class UnavailableCapture:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, None]:
            return False, None

        def release(self) -> None:
            pass

        def get(self, property_id: int) -> float:
            return 0.0

    created = 0

    def capture_factory(_device_index: int) -> UnavailableCapture:
        nonlocal created
        created += 1
        return UnavailableCapture()

    source = WebcamFrameSource(
        0,
        retry_attempts=1,
        capture_factory=capture_factory,
    )
    source.open()

    with pytest.raises(TemporaryFrameSourceError, match="retry policy"):
        source.read()
    assert created == 2
    _assert_status(source.health.status, CameraStatus.DEGRADED)
    assert source.health.temporary_failures == 2
    assert source.health.last_error_code == "temporary_read_failure"
    source.close()
