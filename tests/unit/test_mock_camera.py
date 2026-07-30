from datetime import UTC, datetime

import pytest


def test_mock_frame_source_reads_frames_in_order() -> None:
    from face_profile.camera import Frame, MockFrameSource

    first = Frame(source_id="mock", sequence=1, captured_at=datetime.now(UTC), image=b"one")
    second = Frame(source_id="mock", sequence=2, captured_at=datetime.now(UTC), image=b"two")
    source = MockFrameSource([first, second])

    source.open()

    assert source.read() == first
    assert source.read() == second


def test_mock_frame_source_rejects_read_while_closed() -> None:
    from face_profile.camera import FrameSourceStateError, MockFrameSource

    source = MockFrameSource([])

    with pytest.raises(FrameSourceStateError, match="not open"):
        source.read()


def test_mock_frame_source_reports_exhaustion() -> None:
    from face_profile.camera import EndOfFrames, MockFrameSource

    source = MockFrameSource([])
    source.open()

    with pytest.raises(EndOfFrames):
        source.read()


def test_frame_repr_excludes_image_payload() -> None:
    from face_profile.camera import Frame

    frame = Frame(
        source_id="mock",
        sequence=1,
        captured_at=datetime.now(UTC),
        image=b"private-face-payload",
    )

    assert "private-face-payload" not in repr(frame)
    assert "image=" not in repr(frame)
