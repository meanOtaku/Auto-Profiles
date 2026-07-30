from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest


def test_save_frame_writes_lossless_image(tmp_path: Path) -> None:
    from face_profile.camera import Frame, save_frame

    image = np.array(
        [
            [[0, 0, 255], [0, 255, 0]],
            [[255, 0, 0], [255, 255, 255]],
        ],
        dtype=np.uint8,
    )
    frame = Frame(
        source_id="synthetic",
        sequence=1,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        image=image,
    )
    output = tmp_path / "frame.png"

    save_frame(frame, output)

    decoded = cv2.imread(str(output), cv2.IMREAD_COLOR)
    assert decoded is not None
    np.testing.assert_array_equal(decoded, image)
    assert output.stat().st_mode & 0o077 == 0


def test_save_frame_wraps_encoder_failures(tmp_path: Path) -> None:
    from face_profile.camera import Frame, FrameWriteError, save_frame

    frame = Frame(
        source_id="synthetic",
        sequence=1,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        image=np.zeros((2, 2, 3), dtype=np.uint8),
    )

    with pytest.raises(FrameWriteError, match="failed to save frame"):
        save_frame(frame, tmp_path / "frame.unsupported")
