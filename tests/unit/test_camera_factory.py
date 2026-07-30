from pathlib import Path


def test_create_frame_source_builds_image_adapter() -> None:
    from face_profile.camera import ImageFrameSource
    from face_profile.camera.factory import create_frame_source
    from face_profile.config import CameraConfig

    source = create_frame_source(
        CameraConfig(enabled=True, source="image", path=Path("fixture.png"))
    )

    assert isinstance(source, ImageFrameSource)


def test_create_frame_source_builds_video_adapter() -> None:
    from face_profile.camera import VideoFrameSource
    from face_profile.camera.factory import create_frame_source
    from face_profile.config import CameraConfig

    source = create_frame_source(
        CameraConfig(enabled=True, source="video", path=Path("fixture.avi"))
    )

    assert isinstance(source, VideoFrameSource)


def test_create_frame_source_builds_mock_adapter() -> None:
    from face_profile.camera import MockFrameSource
    from face_profile.camera.factory import create_frame_source
    from face_profile.config import CameraConfig

    source = create_frame_source(CameraConfig(enabled=True, source="mock"))

    assert isinstance(source, MockFrameSource)


def test_create_frame_source_builds_webcam_adapter() -> None:
    from face_profile.camera import WebcamFrameSource
    from face_profile.camera.factory import create_frame_source
    from face_profile.config import CameraConfig

    source = create_frame_source(
        CameraConfig(enabled=True, source="webcam", device_index=2, retry_attempts=4)
    )

    assert isinstance(source, WebcamFrameSource)


def test_create_frame_source_preserves_disabled_safe_default() -> None:
    from face_profile.camera.factory import create_frame_source
    from face_profile.config import CameraConfig

    assert create_frame_source(CameraConfig(enabled=False, source="webcam")) is None
