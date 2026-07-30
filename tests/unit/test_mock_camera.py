from datetime import UTC, datetime

import numpy as np
import pytest


def _assert_status(actual: object, expected: object) -> None:
    assert actual == expected


def test_mock_frame_source_is_deterministic_and_hardware_free() -> None:
    from face_profile.camera import Frame, MockFrameSource

    first = Frame(
        source_id="mock",
        sequence=1,
        captured_at=datetime.now(UTC),
        image=np.zeros((1, 1, 3), dtype=np.uint8),
    )
    second = Frame(
        source_id="mock",
        sequence=2,
        captured_at=datetime.now(UTC),
        image=np.ones((1, 1, 3), dtype=np.uint8),
    )
    source = MockFrameSource([first, second])

    source.open()

    assert source.read() is first
    assert source.read() is second


def test_mock_frame_source_rejects_read_while_closed() -> None:
    from face_profile.camera import FrameSourceStateError, MockFrameSource

    source = MockFrameSource([])

    with pytest.raises(FrameSourceStateError, match="not open"):
        source.read()


def test_mock_frame_source_reports_exhaustion() -> None:
    from face_profile.camera import CameraStatus, EndOfFrames, MockFrameSource

    source = MockFrameSource([])
    source.open()

    with pytest.raises(EndOfFrames):
        source.read()
    _assert_status(source.health.status, CameraStatus.EXHAUSTED)
    source.close()
    _assert_status(source.health.status, CameraStatus.CLOSED)


def test_mock_frame_source_rejects_double_open() -> None:
    from face_profile.camera import FrameSourceStateError, MockFrameSource

    source = MockFrameSource([])
    source.open()

    with pytest.raises(FrameSourceStateError, match="already open"):
        source.open()


def test_frame_repr_excludes_image_payload() -> None:
    from face_profile.camera import Frame

    frame = Frame(
        source_id="mock",
        sequence=1,
        captured_at=datetime.now(UTC),
        image=np.frombuffer(b"private-face-payload", dtype=np.uint8),
    )

    assert "private-face-payload" not in repr(frame)
    assert "image=" not in repr(frame)
