"""In-memory, profile-independent tracking over immutable face detections.

M3 keeps temporary track identity separate from detection and persistent profile identity.
It retains only bounded geometric metadata; it never retains frame pixels, crops, or embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from math import hypot
from typing import Protocol

from face_profile.camera import Frame
from face_profile.config import TrackingConfig
from face_profile.vision.detection import BoundingBox, FaceDetection


class TrackingError(RuntimeError):
    """Raised when a caller supplies an invalid tracking sequence."""


class TrackState(StrEnum):
    """Lifecycle state for one temporary, in-memory track."""

    TRACKING = "tracking"
    LOST = "lost"
    ENDED = "ended"


@dataclass(frozen=True, slots=True)
class TrackSample:
    """Privacy-minimized geometric metadata retained for one observation."""

    sequence: int
    captured_at: datetime
    bounding_box: BoundingBox
    confidence: float


@dataclass(frozen=True, slots=True)
class TrackedFace:
    """A face detection associated with a temporary process-local track."""

    track_id: int
    detection: FaceDetection
    state: TrackState
    age_frames: int
    missed_frames: int
    samples: tuple[TrackSample, ...]


class FaceTracker(Protocol):
    """Associate a frame's detections with temporary, in-memory track IDs."""

    def update(
        self, frame: Frame, detections: tuple[FaceDetection, ...]
    ) -> tuple[TrackedFace, ...]: ...

    def predict_only(self, frame: Frame) -> tuple[TrackedFace, ...]: ...

    def active_tracks(self) -> tuple[TrackedFace, ...]: ...


@dataclass(slots=True)
class _Track:
    track_id: int
    source_id: str
    last_detection: FaceDetection
    last_sequence: int
    last_captured_at: datetime
    velocity_x: float
    velocity_y: float
    age_frames: int
    missed_frames: int
    state: TrackState
    samples: list[TrackSample]


class GeometricFaceTracker:
    """Bounded geometric tracker with deterministic, process-local IDs.

    Association uses a predicted centroid derived from the previous observation's
    velocity together with overlap. It is deliberately independent of appearance,
    embeddings, persistence, and recognition.
    """

    def __init__(
        self,
        *,
        max_missing_frames: int,
        min_iou: float,
        max_center_distance: float,
        max_samples_per_track: int,
        max_tracks: int,
    ) -> None:
        if max_missing_frames < 0:
            raise ValueError("maximum missing frames cannot be negative")
        if not 0.0 <= min_iou <= 1.0:
            raise ValueError("minimum IoU must be between zero and one")
        if max_center_distance <= 0.0:
            raise ValueError("maximum center distance must be positive")
        if max_samples_per_track < 1:
            raise ValueError("maximum samples per track must be positive")
        if max_tracks < 1:
            raise ValueError("maximum tracks must be positive")
        self._max_missing_frames = max_missing_frames
        self._min_iou = min_iou
        self._max_center_distance = max_center_distance
        self._max_samples_per_track = max_samples_per_track
        self._max_tracks = max_tracks
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1
        self._source_id: str | None = None
        self._last_sequence: int | None = None

    def update(
        self, frame: Frame, detections: tuple[FaceDetection, ...]
    ) -> tuple[TrackedFace, ...]:
        """Associate detections, expire stale tracks, and return visible tracks.

        Frames must belong to one source and have strictly increasing sequence
        values. A new tracker instance is required for a new source or stream.
        """

        self._validate_frame(frame)
        self._expire_tracks(frame.sequence)
        pairs = self._associate(frame, detections)
        matched_track_ids = {track_id for track_id, _ in pairs}
        matched_detection_indexes = {detection_index for _, detection_index in pairs}
        required_new_tracks = len(detections) - len(matched_detection_indexes)
        if len(self._tracks) + required_new_tracks > self._max_tracks:
            raise TrackingError("maximum active track count reached")

        visible: list[TrackedFace] = []
        for track_id, detection_index in pairs:
            track = self._tracks[track_id]
            detection = detections[detection_index]
            self._update_track(track, frame, detection)
            visible.append(self._snapshot(track))

        for track_id in sorted(self._tracks):
            if track_id not in matched_track_ids:
                track = self._tracks[track_id]
                track.missed_frames += 1
                track.state = TrackState.LOST

        for detection_index, detection in enumerate(detections):
            if detection_index not in matched_detection_indexes:
                track = self._start_track(frame, detection)
                visible.append(self._snapshot(track))

        self._expire_tracks(frame.sequence)
        self._last_sequence = frame.sequence
        return tuple(sorted(visible, key=lambda tracked: tracked.track_id))

    def predict_only(self, frame: Frame) -> tuple[TrackedFace, ...]:
        """Advance every track's geometry by motion prediction, without detections.

        M15's adaptive detection frequency uses this to skip the (costly)
        detector on some frames while keeping tracks from expiring due to a
        gap. The returned boxes are predicted, not observed: landmarks are
        carried over unchanged from the last real detection and are
        approximate. Callers must not feed a predicted result into
        quality/alignment/embedding/recognition — those require a real
        detection from :meth:`update`; predicted frames exist only for
        track continuity and presence bookkeeping.
        """

        self._validate_frame(frame)
        self._expire_tracks(frame.sequence)
        visible: list[TrackedFace] = []
        for track_id in sorted(self._tracks):
            track = self._tracks[track_id]
            predicted_box = _predicted_box(track, frame.sequence)
            predicted_detection = replace(track.last_detection, bounding_box=predicted_box)
            track.missed_frames += 1
            track.state = TrackState.LOST
            visible.append(
                TrackedFace(
                    track_id=track_id,
                    detection=predicted_detection,
                    state=track.state,
                    age_frames=track.age_frames,
                    missed_frames=track.missed_frames,
                    samples=tuple(track.samples),
                )
            )
        self._expire_tracks(frame.sequence)
        self._last_sequence = frame.sequence
        return tuple(visible)

    def active_tracks(self) -> tuple[TrackedFace, ...]:
        """Return privacy-minimized snapshots of tracks that have not ended."""

        return tuple(
            self._snapshot(track)
            for _, track in sorted(self._tracks.items())
            if track.state is not TrackState.ENDED
        )

    def _validate_frame(self, frame: Frame) -> None:
        if frame.sequence < 0:
            raise TrackingError("frame sequence cannot be negative")
        if self._source_id is None:
            self._source_id = frame.source_id
        elif frame.source_id != self._source_id:
            raise TrackingError("tracker cannot mix frame sources")
        if self._last_sequence is not None and frame.sequence <= self._last_sequence:
            raise TrackingError("frame sequence must increase")

    def _associate(
        self,
        frame: Frame,
        detections: tuple[FaceDetection, ...],
    ) -> tuple[tuple[int, int], ...]:
        candidates: list[tuple[float, int, int]] = []
        for track_id, track in self._tracks.items():
            if track.state is TrackState.ENDED:
                continue
            predicted = _predicted_box(track, frame.sequence)
            for detection_index, detection in enumerate(detections):
                iou = _intersection_over_union(predicted, detection.bounding_box)
                normalized_distance = _normalized_center_distance(predicted, detection.bounding_box)
                if iou < self._min_iou or normalized_distance > self._max_center_distance:
                    continue
                # Prefer velocity-consistent observations, then overlap, then stable IDs.
                cost = normalized_distance - iou
                candidates.append((cost, track_id, detection_index))
        pairs: list[tuple[int, int]] = []
        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        for _, track_id, detection_index in sorted(candidates):
            if track_id in used_tracks or detection_index in used_detections:
                continue
            used_tracks.add(track_id)
            used_detections.add(detection_index)
            pairs.append((track_id, detection_index))
        return tuple(pairs)

    def _start_track(self, frame: Frame, detection: FaceDetection) -> _Track:
        if len(self._tracks) >= self._max_tracks:
            raise TrackingError("maximum active track count reached")
        track = _Track(
            track_id=self._next_track_id,
            source_id=frame.source_id,
            last_detection=detection,
            last_sequence=frame.sequence,
            last_captured_at=frame.captured_at,
            velocity_x=0.0,
            velocity_y=0.0,
            age_frames=1,
            missed_frames=0,
            state=TrackState.TRACKING,
            samples=[_sample(frame, detection)],
        )
        self._tracks[track.track_id] = track
        self._next_track_id += 1
        return track

    def _update_track(self, track: _Track, frame: Frame, detection: FaceDetection) -> None:
        elapsed_frames = max(1, frame.sequence - track.last_sequence)
        old_center = _center(track.last_detection.bounding_box)
        new_center = _center(detection.bounding_box)
        track.velocity_x = (new_center[0] - old_center[0]) / elapsed_frames
        track.velocity_y = (new_center[1] - old_center[1]) / elapsed_frames
        track.last_detection = detection
        track.last_sequence = frame.sequence
        track.last_captured_at = frame.captured_at
        track.age_frames += 1
        track.missed_frames = 0
        track.state = TrackState.TRACKING
        track.samples.append(_sample(frame, detection))
        del track.samples[: -self._max_samples_per_track]

    def _expire_tracks(self, current_sequence: int) -> None:
        for track_id, track in tuple(self._tracks.items()):
            gap = current_sequence - track.last_sequence
            if gap > self._max_missing_frames:
                track.state = TrackState.ENDED
                del self._tracks[track_id]

    @staticmethod
    def _snapshot(track: _Track) -> TrackedFace:
        return TrackedFace(
            track_id=track.track_id,
            detection=track.last_detection,
            state=track.state,
            age_frames=track.age_frames,
            missed_frames=track.missed_frames,
            samples=tuple(track.samples),
        )


def create_tracker(config: TrackingConfig) -> GeometricFaceTracker | None:
    """Create the configured tracker only when M3 is explicitly enabled."""

    if not config.enabled:
        return None
    return GeometricFaceTracker(
        max_missing_frames=config.max_missing_frames,
        min_iou=config.min_iou,
        max_center_distance=config.max_center_distance,
        max_samples_per_track=config.max_samples_per_track,
        max_tracks=config.max_tracks,
    )


def _sample(frame: Frame, detection: FaceDetection) -> TrackSample:
    return TrackSample(
        sequence=frame.sequence,
        captured_at=frame.captured_at,
        bounding_box=detection.bounding_box,
        confidence=detection.confidence,
    )


def _center(box: BoundingBox) -> tuple[float, float]:
    return (box.x + box.width / 2.0, box.y + box.height / 2.0)


def _predicted_box(track: _Track, sequence: int) -> BoundingBox:
    elapsed = max(0, sequence - track.last_sequence)
    box = track.last_detection.bounding_box
    return BoundingBox(
        x=box.x + track.velocity_x * elapsed,
        y=box.y + track.velocity_y * elapsed,
        width=box.width,
        height=box.height,
    )


def _intersection_over_union(left: BoundingBox, right: BoundingBox) -> float:
    intersection_left = max(left.x, right.x)
    intersection_top = max(left.y, right.y)
    intersection_right = min(left.x + left.width, right.x + right.width)
    intersection_bottom = min(left.y + left.height, right.y + right.height)
    if intersection_right <= intersection_left or intersection_bottom <= intersection_top:
        return 0.0
    intersection = (intersection_right - intersection_left) * (
        intersection_bottom - intersection_top
    )
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union > 0.0 else 0.0


def _normalized_center_distance(left: BoundingBox, right: BoundingBox) -> float:
    left_center = _center(left)
    right_center = _center(right)
    scale = max(left.width, left.height, right.width, right.height)
    return hypot(left_center[0] - right_center[0], left_center[1] - right_center[1]) / scale
