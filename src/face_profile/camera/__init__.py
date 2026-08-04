"""Camera boundary types and deterministic test adapter."""

import os
import platform
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeAlias, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from face_profile.camera.diagnostics import diagnose_webcam
from face_profile.platform_security import (
    SecurityBoundaryError,
    ensure_private_directory,
    harden_new_file,
    posix_open_flags,
    reject_unsafe_target,
)

ImageArray: TypeAlias = NDArray[np.uint8]


def _validated_image(image: object) -> ImageArray:
    if not isinstance(image, np.ndarray):
        raise FrameSourceError("frame source returned an invalid image")
    if image.dtype != np.uint8 or image.ndim not in {2, 3}:
        raise FrameSourceError("frame source returned an invalid image")
    return cast(ImageArray, image)


class CameraStatus(StrEnum):
    """Operational state exposed without leaking backend errors."""

    CLOSED = "closed"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class CameraHealth:
    """Immutable, privacy-safe camera health snapshot."""

    status: CameraStatus
    frames_read: int
    temporary_failures: int
    last_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class Frame:
    """A timestamped decoded frame at the M1 camera boundary."""

    source_id: str
    sequence: int
    captured_at: datetime
    image: ImageArray = field(repr=False)


class FrameWriteError(OSError):
    """Raised when an image frame cannot be encoded and saved."""


def save_frame(frame: Frame, path: Path) -> None:
    """Save a frame using the format selected by the output suffix.

    Debug/detection overlays are sensitive derived images (ARCHITECTURE.md
    §13), so this is the one private, no-follow frame-output boundary
    every debug-output caller must use. M17 extends the owner-only
    protection to Windows (a protected DACL, since ``os.fchmod``/``chmod``
    mode bits do nothing there) via ``face_profile.platform_security``,
    on top of the pre-existing POSIX ``O_NOFOLLOW``/``0600`` enforcement.
    """

    try:
        encoded, payload = cv2.imencode(path.suffix, frame.image)
    except cv2.error as error:
        raise FrameWriteError("failed to save frame") from error
    if not encoded:
        raise FrameWriteError("failed to save frame")

    try:
        ensure_private_directory(path.parent)
        reject_unsafe_target(path)
        if path.exists():
            # Tighten an existing output before truncating it, so its old
            # DACL/mode cannot expose newly written frame bytes even briefly.
            harden_new_file(path)
    except SecurityBoundaryError as error:
        raise FrameWriteError("failed to save frame") from error

    flags = posix_open_flags(truncate=True, exclusive=False)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(payload.tobytes())
    except OSError as error:
        raise FrameWriteError("failed to save frame") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)

    try:
        harden_new_file(path)
    except SecurityBoundaryError as error:
        raise FrameWriteError("failed to save frame") from error


class EndOfFrames(EOFError):
    """Raised when a deterministic frame source has no frames remaining."""


class FrameSourceStateError(RuntimeError):
    """Raised when a frame source operation violates its lifecycle."""


class FrameSourceError(RuntimeError):
    """Raised when a frame source cannot acquire a valid frame."""


class TemporaryFrameSourceError(FrameSourceError):
    """Raised after bounded recovery attempts cannot acquire a frame."""


class CaptureDevice(Protocol):
    """Narrow OpenCV capture interface used for deterministic adapters."""

    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, object]: ...

    def release(self) -> None: ...

    def get(self, property_id: int) -> float: ...


class FrameSourceLifecycle(Protocol):
    """Minimal lifecycle required by the service coordinator."""

    def open(self) -> None: ...

    def read(self) -> Frame: ...

    def close(self) -> None: ...


class FrameSource(FrameSourceLifecycle, Protocol):
    """Camera source contract including health reporting."""

    @property
    def health(self) -> CameraHealth: ...


class ImageFrameSource:
    """Read one decoded frame from an image file."""

    def __init__(
        self,
        path: Path,
        *,
        source_id: str = "image",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = path
        self._source_id = source_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._image: ImageArray | None = None
        self._consumed = False
        self._status = CameraStatus.CLOSED
        self._frames_read = 0
        self._last_error_code: str | None = None

    @property
    def health(self) -> CameraHealth:
        return CameraHealth(
            status=self._status,
            frames_read=self._frames_read,
            temporary_failures=0,
            last_error_code=self._last_error_code,
        )

    def open(self) -> None:
        if self._image is not None:
            raise FrameSourceStateError("frame source is already open")
        if not self._path.is_file():
            self._status = CameraStatus.FAILED
            self._last_error_code = "open_failed"
            raise FrameSourceError("unable to open image source")
        try:
            image = cv2.imread(str(self._path), cv2.IMREAD_COLOR)
        except cv2.error as error:
            self._status = CameraStatus.FAILED
            self._last_error_code = "backend_decode_error"
            raise FrameSourceError("image backend decode failed") from error
        if image is None:
            self._status = CameraStatus.FAILED
            self._last_error_code = "open_failed"
            raise FrameSourceError("unable to open image source")
        try:
            self._image = _validated_image(image)
        except FrameSourceError:
            self._status = CameraStatus.FAILED
            self._last_error_code = "invalid_frame"
            raise
        self._consumed = False
        self._status = CameraStatus.HEALTHY
        self._frames_read = 0
        self._last_error_code = None

    def read(self) -> Frame:
        if self._image is None:
            raise FrameSourceStateError("frame source is not open")
        if self._consumed:
            self._status = CameraStatus.EXHAUSTED
            raise EndOfFrames
        self._consumed = True
        self._frames_read += 1
        return Frame(
            source_id=self._source_id,
            sequence=0,
            captured_at=self._clock(),
            image=self._image.copy(),
        )

    def close(self) -> None:
        self._image = None
        self._status = CameraStatus.CLOSED


class VideoFrameSource:
    """Read decoded frames sequentially from a video file."""

    def __init__(
        self,
        path: Path,
        *,
        source_id: str = "video",
        clock: Callable[[], datetime] | None = None,
        capture_factory: Callable[[str], CaptureDevice] | None = None,
    ) -> None:
        self._path = path
        self._source_id = source_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capture_factory = capture_factory or self._default_capture_factory
        self._capture: CaptureDevice | None = None
        self._sequence = 0
        self._status = CameraStatus.CLOSED
        self._last_error_code: str | None = None

    @property
    def health(self) -> CameraHealth:
        return CameraHealth(
            status=self._status,
            frames_read=self._sequence,
            temporary_failures=0,
            last_error_code=self._last_error_code,
        )

    @staticmethod
    def _default_capture_factory(path: str) -> CaptureDevice:
        return cast(CaptureDevice, cv2.VideoCapture(path))

    def open(self) -> None:
        if self._capture is not None:
            raise FrameSourceStateError("frame source is already open")
        capture: CaptureDevice | None = None
        try:
            capture = self._capture_factory(str(self._path))
            opened = capture.isOpened()
        except Exception as error:
            if capture is not None:
                with suppress(Exception):
                    capture.release()
            self._status = CameraStatus.FAILED
            self._last_error_code = "backend_open_error"
            raise FrameSourceError("video backend open failed") from error
        if not opened:
            capture.release()
            self._status = CameraStatus.FAILED
            self._last_error_code = "open_failed"
            raise FrameSourceError("unable to open video source")
        self._capture = capture
        self._sequence = 0
        self._status = CameraStatus.HEALTHY
        self._last_error_code = None

    def read(self) -> Frame:
        if self._capture is None:
            raise FrameSourceStateError("frame source is not open")
        try:
            available, image = self._capture.read()
        except Exception as error:
            self._status = CameraStatus.FAILED
            self._last_error_code = "backend_read_error"
            raise FrameSourceError("video backend read failed") from error
        if not available or image is None:
            try:
                frame_count = self._capture.get(cv2.CAP_PROP_FRAME_COUNT)
                position = self._capture.get(cv2.CAP_PROP_POS_FRAMES)
            except Exception as error:
                self._status = CameraStatus.FAILED
                self._last_error_code = "backend_metadata_error"
                raise FrameSourceError("video backend metadata failed") from error
            has_known_count = frame_count > 0
            if (has_known_count and position < frame_count) or (
                not has_known_count and self._sequence == 0
            ):
                self._status = CameraStatus.FAILED
                self._last_error_code = "read_failed"
                raise FrameSourceError("video frame unavailable before expected termination")
            self._status = CameraStatus.EXHAUSTED
            raise EndOfFrames
        try:
            validated_image = _validated_image(image)
        except FrameSourceError:
            self._status = CameraStatus.FAILED
            self._last_error_code = "invalid_frame"
            raise
        frame = Frame(
            source_id=self._source_id,
            sequence=self._sequence,
            captured_at=self._clock(),
            image=validated_image,
        )
        self._sequence += 1
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._status = CameraStatus.CLOSED


class WebcamFrameSource:
    """Read webcam frames with bounded reconnect attempts."""

    def __init__(
        self,
        device_index: int,
        *,
        retry_attempts: int = 2,
        source_id: str = "webcam",
        capture_factory: Callable[[int], CaptureDevice] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retry_attempts < 0:
            raise ValueError("retry_attempts must be non-negative")
        self._device_index = device_index
        self._retry_attempts = retry_attempts
        self._source_id = source_id
        self._capture_factory = capture_factory or self._default_capture_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capture: CaptureDevice | None = None
        self._sequence = 0
        self._status = CameraStatus.CLOSED
        self._temporary_failures = 0
        self._last_error_code: str | None = None

    @property
    def health(self) -> CameraHealth:
        return CameraHealth(
            status=self._status,
            frames_read=self._sequence,
            temporary_failures=self._temporary_failures,
            last_error_code=self._last_error_code,
        )

    @staticmethod
    def _default_capture_factory(device_index: int) -> CaptureDevice:
        # Pin an explicit backend per platform rather than trusting OpenCV's
        # default auto-detection, so the same device_index deterministically
        # addresses the same backend across runs instead of silently
        # cascading to whichever backend OpenCV happens to probe first.
        system = platform.system()
        if system == "Linux":
            # On embedded/aarch64 Linux (e.g. Jetson JetPack Ubuntu), a USB
            # UVC webcam is a V4L2 device and pinning the backend avoids
            # ambiguity if other video I/O backends are present but not
            # applicable (e.g. GStreamer pipelines meant for CSI cameras,
            # not USB webcams).
            return cast(CaptureDevice, cv2.VideoCapture(device_index, cv2.CAP_V4L2))
        if system == "Windows":
            # DirectShow is the deterministic, broadly-compatible OpenCV
            # backend for USB UVC webcams on Windows 10/11 (M17). Media
            # Foundation (CAP_MSMF) is OpenCV's newer alternative but has
            # documented compatibility gaps with some UVC drivers; DirectShow
            # is the one this project pins and documents (see
            # docs/RUNNING_ON_WINDOWS.md), not left to auto-detection.
            return cast(CaptureDevice, cv2.VideoCapture(device_index, cv2.CAP_DSHOW))
        return cast(CaptureDevice, cv2.VideoCapture(device_index))

    def _connect(self) -> None:
        capture: CaptureDevice | None = None
        try:
            if self._capture is not None:
                self._capture.release()
                self._capture = None
            capture = self._capture_factory(self._device_index)
            opened = capture.isOpened()
        except Exception as error:
            if capture is not None:
                with suppress(Exception):
                    capture.release()
            self._status = CameraStatus.FAILED
            self._last_error_code = "backend_open_error"
            raise FrameSourceError("webcam backend open failed") from error
        if not opened:
            capture.release()
            self._capture = None
            self._status = CameraStatus.FAILED
            self._last_error_code = "open_failed"
            diagnostics = diagnose_webcam(self._device_index)
            raise FrameSourceError(
                f"unable to open webcam source: {self._device_index} ({diagnostics.hint})"
            )
        self._capture = capture

    def open(self) -> None:
        if self._capture is not None:
            raise FrameSourceStateError("frame source is already open")
        self._connect()
        self._sequence = 0
        self._status = CameraStatus.HEALTHY
        self._temporary_failures = 0
        self._last_error_code = None

    def read(self) -> Frame:
        if self._capture is None:
            raise FrameSourceStateError("frame source is not open")
        for attempt in range(self._retry_attempts + 1):
            error_code = "temporary_read_failure"
            try:
                available, image = self._capture.read()
            except Exception:
                available, image = False, None
                error_code = "backend_read_error"
            if available and image is not None:
                try:
                    validated_image = _validated_image(image)
                except FrameSourceError:
                    error_code = "invalid_frame"
                else:
                    self._status = CameraStatus.HEALTHY
                    frame = Frame(
                        source_id=self._source_id,
                        sequence=self._sequence,
                        captured_at=self._clock(),
                        image=validated_image,
                    )
                    self._sequence += 1
                    return frame
            self._status = CameraStatus.DEGRADED
            self._temporary_failures += 1
            self._last_error_code = error_code
            if attempt < self._retry_attempts:
                self._connect()
        raise TemporaryFrameSourceError("webcam frame unavailable after retry policy")

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._status = CameraStatus.CLOSED


class MockFrameSource:
    """Deterministic in-memory frame source for automated tests."""

    def __init__(self, frames: Iterable[Frame]) -> None:
        self._frames = tuple(frames)
        self._index = 0
        self._is_open = False
        self._status = CameraStatus.CLOSED

    @property
    def health(self) -> CameraHealth:
        return CameraHealth(
            status=self._status,
            frames_read=self._index,
            temporary_failures=0,
        )

    def open(self) -> None:
        if self._is_open:
            raise FrameSourceStateError("frame source is already open")
        self._is_open = True
        self._index = 0
        self._status = CameraStatus.HEALTHY

    def read(self) -> Frame:
        if not self._is_open:
            raise FrameSourceStateError("frame source is not open")
        if self._index >= len(self._frames):
            self._status = CameraStatus.EXHAUSTED
            raise EndOfFrames
        frame = self._frames[self._index]
        self._index += 1
        return frame

    def close(self) -> None:
        self._is_open = False
        self._status = CameraStatus.CLOSED
