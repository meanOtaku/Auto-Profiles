"""Camera boundary types and deterministic test adapter."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Frame:
    """A timestamped frame payload at the M0 boundary."""

    source_id: str
    sequence: int
    captured_at: datetime
    image: bytes = field(repr=False)


class EndOfFrames(EOFError):
    """Raised when a deterministic frame source has no frames remaining."""


class FrameSourceStateError(RuntimeError):
    """Raised when a frame source operation violates its lifecycle."""


class FrameSource(Protocol):
    """Interface implemented by camera and fixture sources."""

    def open(self) -> None: ...

    def read(self) -> Frame: ...

    def close(self) -> None: ...


class MockFrameSource:
    """Deterministic in-memory frame source for automated tests."""

    def __init__(self, frames: Iterable[Frame]) -> None:
        self._frames = tuple(frames)
        self._index = 0
        self._is_open = False

    def open(self) -> None:
        self._is_open = True
        self._index = 0

    def read(self) -> Frame:
        if not self._is_open:
            raise FrameSourceStateError("frame source is not open")
        if self._index >= len(self._frames):
            raise EndOfFrames
        frame = self._frames[self._index]
        self._index += 1
        return frame

    def close(self) -> None:
        self._is_open = False
