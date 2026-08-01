"""Bounded, in-memory retention of the best accepted crop per track.

M4 requires that the best quality-accepted crop is retained per track so a
later milestone can use one representative sample instead of every frame.
This module retains only one aligned crop per track, in process memory, and
discards it as soon as the caller reports the track has ended. It never
writes to disk, never associates a crop with a profile, and is not itself
enrollment or persistence: those remain M6/M8 concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from face_profile.vision.alignment import AlignedFace
from face_profile.vision.quality import QualityResult


class BestSampleError(RuntimeError):
    """Raised when best-sample retention is used outside its bounds."""


@dataclass(frozen=True, slots=True)
class BestFaceSample:
    """The highest-quality accepted crop observed so far for one track."""

    track_id: int
    aligned: AlignedFace
    quality: QualityResult
    sequence: int
    captured_at: datetime


class BestSampleTracker:
    """Keep the single best accepted sample per track, bounded by track count."""

    def __init__(self, *, max_tracks: int) -> None:
        if max_tracks < 1:
            raise ValueError("max_tracks must be positive")
        self._max_tracks = max_tracks
        self._best: dict[int, BestFaceSample] = {}

    def consider(
        self,
        *,
        track_id: int,
        aligned: AlignedFace,
        quality: QualityResult,
        sequence: int,
        captured_at: datetime,
    ) -> None:
        """Replace the retained sample for a track only if quality improves.

        Only accepted samples may be considered; rejected samples must never
        reach best-crop retention so poor samples stay excluded from
        downstream enrollment and matching.
        """

        if not quality.accepted:
            raise BestSampleError("only accepted samples may be retained")
        existing = self._best.get(track_id)
        if existing is not None and existing.quality.score >= quality.score:
            return
        if existing is None and len(self._best) >= self._max_tracks:
            raise BestSampleError("maximum tracked sample count reached")
        self._best[track_id] = BestFaceSample(
            track_id=track_id,
            aligned=aligned,
            quality=quality,
            sequence=sequence,
            captured_at=captured_at,
        )

    def get(self, track_id: int) -> BestFaceSample | None:
        """Return the retained best sample for a track, if any."""

        return self._best.get(track_id)

    def discard(self, track_id: int) -> None:
        """Drop the retained sample for a track that has ended."""

        self._best.pop(track_id, None)

    def samples(self) -> dict[int, BestFaceSample]:
        """Return a shallow copy of all currently retained samples."""

        return dict(self._best)
