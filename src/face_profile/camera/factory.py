"""Construct frame-source adapters from validated configuration."""

from face_profile.camera import (
    FrameSource,
    ImageFrameSource,
    MockFrameSource,
    VideoFrameSource,
    WebcamFrameSource,
)
from face_profile.config import CameraConfig


def create_frame_source(config: CameraConfig) -> FrameSource | None:
    """Build the selected source, preserving the disabled safe default."""

    if not config.enabled:
        return None
    if config.source == "image":
        if config.path is None:  # Defensive boundary for non-Pydantic callers.
            raise ValueError("image source requires a path")
        return ImageFrameSource(config.path)
    if config.source == "video":
        if config.path is None:
            raise ValueError("video source requires a path")
        return VideoFrameSource(config.path)
    if config.source == "mock":
        return MockFrameSource([])
    if config.source == "webcam":
        return WebcamFrameSource(
            config.device_index,
            retry_attempts=config.retry_attempts,
        )
    raise NotImplementedError(f"camera source is not implemented: {config.source}")
