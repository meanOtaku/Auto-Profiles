"""M12's background pipeline worker: camera through settings, in one thread.

This is a deliberately simple threaded polling loop (open source, read
frame, run the M2-M11 stages, sleep, repeat), not the bounded-queue,
multi-worker architecture ARCHITECTURE.md §5 describes as the eventual
target — adaptive frequency, batching, and queue backpressure are
explicitly M15's job. This loop still provides real pause/resume,
graceful shutdown, and health reporting, and has only been smoke-tested
with mock frame sources and mock backends, never real hardware.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast
from uuid import UUID

import cv2

from face_profile.camera import (
    EndOfFrames,
    Frame,
    FrameSourceLifecycle,
    ImageArray,
    TemporaryFrameSourceError,
)
from face_profile.database.models import Candidate, CandidateStatus
from face_profile.database.repository import ProfileNotFoundError, ProfileRepository
from face_profile.enrollment.manager import CandidateManager
from face_profile.liveness.passive import PassiveLivenessEvaluator
from face_profile.presence.active_user import ActiveUserSelector, PresenceCandidate
from face_profile.presence.builder import build_presence_candidate
from face_profile.recognition.decision import RecognitionDecision, RecognitionState
from face_profile.recognition.recognizer import KnownPersonRecognizer
from face_profile.settings.active_profile_applier import ActiveProfileSettingsApplier
from face_profile.settings.last_used_service import LastUsedPreferenceService
from face_profile.vision.alignment import FaceAligner
from face_profile.vision.detection import BoundingBox, FaceDetector
from face_profile.vision.embedding import EmbeddingGenerator
from face_profile.vision.quality import QualityEvaluator
from face_profile.vision.tracking import FaceTracker, TrackedFace

logger = logging.getLogger("face_profile.api.worker")

_PREVIEW_LABEL_MAX_LENGTH = 80


class PreviewCategory(StrEnum):
    """Visual status category drawn for one tracked face in the preview overlay."""

    KNOWN = "known"
    UNKNOWN = "unknown"
    LIVENESS_FAILED = "liveness_failed"


_PREVIEW_COLORS_BGR: dict[PreviewCategory, tuple[int, int, int]] = {
    PreviewCategory.KNOWN: (0, 170, 0),
    PreviewCategory.UNKNOWN: (0, 200, 200),
    PreviewCategory.LIVENESS_FAILED: (0, 0, 220),
}


@dataclass(frozen=True, slots=True)
class _PreviewLabel:
    """One drawable box/label for the preview overlay; ``text`` is pre-sanitized."""

    bounding_box: BoundingBox
    text: str
    category: PreviewCategory


@dataclass(frozen=True, slots=True)
class _FaceObservation:
    """One track's current recognition state, independent of preview drawing."""

    category: PreviewCategory
    profile_id: UUID | None
    candidate_id: UUID | None
    display_name: str | None


@dataclass(frozen=True, slots=True)
class FaceSnapshot:
    """Privacy-safe, bounded metadata for one currently-tracked face.

    Exposed through ``GET /api/v1/faces``. Deliberately excludes images,
    crops, embeddings, and any field beyond track/profile/candidate
    identifiers, a display label, quality, and timestamps -- see
    ARCHITECTURE.md §13 and CODING_STANDARDS.md §18.
    """

    track_id: int
    state: PreviewCategory
    profile_id: UUID | None
    candidate_id: UUID | None
    display_label: str | None
    quality_score: float | None
    last_observed_at: datetime


def _sanitize_preview_text(text: str) -> str:
    """Bound and clean text before it is drawn on the preview overlay.

    Display names and candidate temporary names come from data an operator
    or the deterministic naming scheme controls, not from an untrusted
    network caller, but neither is length- or charset-bounded upstream (see
    ``database/repository.py``'s ``create``/``update``, which only reject an
    empty name). Without this, a hostile or absurdly long name could bloat
    the annotated frame or draw incorrectly; see CODING_STANDARDS.md §18.
    """

    printable = "".join(character for character in text if character.isprintable())
    collapsed = " ".join(printable.split())
    if len(collapsed) > _PREVIEW_LABEL_MAX_LENGTH:
        collapsed = collapsed[: _PREVIEW_LABEL_MAX_LENGTH - 1] + "…"
    return collapsed or "Unknown"


_UNKNOWN_OBSERVATION = _FaceObservation(PreviewCategory.UNKNOWN, None, None, None)


def _preview_label_from_observation(
    box: BoundingBox, observation: _FaceObservation
) -> _PreviewLabel:
    """Render one track's observation into a drawable preview box/label."""

    if observation.category is PreviewCategory.KNOWN:
        text = _sanitize_preview_text(f"{observation.display_name} ({observation.profile_id})")
        return _PreviewLabel(box, text, PreviewCategory.KNOWN)
    if observation.category is PreviewCategory.LIVENESS_FAILED:
        base = (
            f"{observation.display_name} ({observation.candidate_id})"
            if observation.candidate_id is not None
            else "Unknown"
        )
        text = _sanitize_preview_text(f"{base} - liveness failed")
        return _PreviewLabel(box, text, PreviewCategory.LIVENESS_FAILED)
    if observation.candidate_id is not None:
        text = _sanitize_preview_text(f"{observation.display_name} ({observation.candidate_id})")
        return _PreviewLabel(box, text, PreviewCategory.UNKNOWN)
    return _PreviewLabel(box, "Unknown", PreviewCategory.UNKNOWN)


def _draw_preview_label(image: ImageArray, label: _PreviewLabel, *, scale: float) -> None:
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        return
    box = label.bounding_box
    x1 = int(min(max(round(box.x * scale), 0), width - 1))
    y1 = int(min(max(round(box.y * scale), 0), height - 1))
    x2 = int(min(max(round((box.x + box.width) * scale), 0), width - 1))
    y2 = int(min(max(round((box.y + box.height) * scale), 0), height - 1))
    color = _PREVIEW_COLORS_BGR[label.category]
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    text_y = y1 - 6 if y1 - 6 > 10 else min(y1 + 16, height - 1)
    cv2.putText(
        image,
        label.text,
        (x1, text_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        1,
        cv2.LINE_AA,
    )


class WorkerState(StrEnum):
    """Observable background-worker lifecycle."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    """Immutable, privacy-safe worker health snapshot."""

    state: WorkerState
    frames_processed: int
    frames_detected: int
    last_error: str | None


class PipelineWorker:
    """Runs the detect->track->quality->align->embed->recognize->... loop.

    When ``preview_enabled`` is set, each frame that runs a real detection
    (not a ``predict_only`` M15 skip) may also render a bounded-rate,
    max-width-downscaled, annotated JPEG copy of that frame -- boxes and
    privacy-safe labels only -- into a single-slot in-memory snapshot
    (:meth:`latest_preview_jpeg`), always replacing rather than
    accumulating. The pipeline's own source frame is never mutated for
    this: every draw happens on ``frame.image.copy()``.
    """

    def __init__(
        self,
        *,
        frame_source: FrameSourceLifecycle,
        detector: FaceDetector,
        tracker: FaceTracker,
        quality_evaluator: QualityEvaluator | None = None,
        aligner: FaceAligner | None = None,
        embedder: EmbeddingGenerator | None = None,
        recognizer: KnownPersonRecognizer | None = None,
        candidate_manager: CandidateManager | None = None,
        active_user_selector: ActiveUserSelector | None = None,
        settings_applier: ActiveProfileSettingsApplier | None = None,
        preference_service: LastUsedPreferenceService | None = None,
        passive_liveness_evaluator: PassiveLivenessEvaluator | None = None,
        profiles: ProfileRepository | None = None,
        poll_interval_seconds: float = 0.1,
        detection_interval_frames: int = 1,
        preview_enabled: bool = False,
        preview_jpeg_quality: int = 70,
        preview_max_fps: float = 5.0,
        preview_max_width: int = 640,
    ) -> None:
        if detection_interval_frames < 1:
            raise ValueError("detection_interval_frames must be positive")
        if preview_max_fps <= 0.0:
            raise ValueError("preview_max_fps must be positive")
        if preview_max_width < 1:
            raise ValueError("preview_max_width must be positive")
        self._frame_source = frame_source
        self._detector = detector
        self._tracker = tracker
        self._quality_evaluator = quality_evaluator
        self._aligner = aligner
        self._embedder = embedder
        self._recognizer = recognizer
        self._candidate_manager = candidate_manager
        self._active_user_selector = active_user_selector
        self._settings_applier = settings_applier
        self._preference_service = preference_service
        self._passive_liveness_evaluator = passive_liveness_evaluator
        self._profiles = profiles
        self._poll_interval_seconds = poll_interval_seconds
        self._detection_interval_frames = detection_interval_frames
        self._preview_enabled = preview_enabled
        self._preview_jpeg_quality = preview_jpeg_quality
        self._preview_max_width = preview_max_width
        self._preview_min_interval_seconds = 1.0 / preview_max_fps

        self._state = WorkerState.STOPPED
        self._frames_processed = 0
        self._frames_detected = 0
        self._last_error: str | None = None
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._known_track_ids: set[int] = set()
        self._lock = threading.Lock()
        self._preview_last_generated_monotonic = 0.0
        self._latest_preview_jpeg: bytes | None = None
        self._latest_faces: dict[int, FaceSnapshot] = {}

    def start(self) -> None:
        """Open the frame source and start the background thread."""

        if self._thread is not None:
            raise RuntimeError("worker is already started")
        self._frame_source.open()
        self._state = WorkerState.RUNNING
        self._stop_event.clear()
        self._pause_event.clear()
        self._thread = threading.Thread(target=self._run, name="pipeline-worker", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Pause processing: the frame source stays open, but no new frames are drawn.

        Privacy control, not a camera-off control (HERMES.md's UI rule):
        clears the cached preview snapshot immediately so
        ``GET /api/v1/preview/latest.jpg`` stops serving a stale frame while
        paused, rather than only stopping generation of new ones.
        """

        self._pause_event.set()
        with self._lock:
            if self._state is WorkerState.RUNNING:
                self._state = WorkerState.PAUSED
            self._latest_preview_jpeg = None

    def resume(self) -> None:
        self._pause_event.clear()
        with self._lock:
            if self._state is WorkerState.PAUSED:
                self._state = WorkerState.RUNNING

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal shutdown, join the thread, and close the frame source."""

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        try:
            self._frame_source.close()
        finally:
            with self._lock:
                self._state = WorkerState.STOPPED
                # A stopped worker produces no new frames; drop the stale
                # snapshot rather than keep serving it indefinitely.
                self._latest_preview_jpeg = None
                self._latest_faces = {}

    def health(self) -> WorkerHealth:
        with self._lock:
            return WorkerHealth(
                state=self._state,
                frames_processed=self._frames_processed,
                frames_detected=self._frames_detected,
                last_error=self._last_error,
            )

    def latest_preview_jpeg(self) -> bytes | None:
        """Return the most recently generated annotated JPEG snapshot, if any.

        The returned ``bytes`` object is immutable and safe to hand directly
        to a caller: internal state is always replaced wholesale under the
        lock, never mutated in place, so no caller can observe a partially
        written frame and no mutable array ever leaves this class.
        """

        with self._lock:
            return self._latest_preview_jpeg

    def latest_faces(self) -> tuple[FaceSnapshot, ...]:
        """Return a bounded, privacy-safe snapshot of every currently-tracked face.

        Backs ``GET /api/v1/faces``. Entries are removed as soon as their
        track ends (see ``_process_one_frame``), so this never grows
        unbounded and never reports a face that is no longer present.
        """

        with self._lock:
            return tuple(self._latest_faces.values())

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(self._poll_interval_seconds)
                continue
            try:
                self._process_one_frame()
            except EndOfFrames:
                break
            except TemporaryFrameSourceError as error:
                logger.warning(
                    "worker frame source temporarily unavailable",
                    extra={"event_type": "WorkerFrameError", "error_code": type(error).__name__},
                )
            except Exception as error:
                # Isolate the worker thread: an unhandled error must mark
                # the worker FAILED for health reporting, not silently
                # crash the background thread or the API process.
                with self._lock:
                    self._state = WorkerState.FAILED
                    self._last_error = type(error).__name__
                logger.error(
                    "worker processing failed",
                    extra={"event_type": "WorkerFailed", "error_code": type(error).__name__},
                )
                break
            time.sleep(self._poll_interval_seconds)
        with self._lock:
            if self._state is not WorkerState.FAILED:
                self._state = WorkerState.STOPPED

    def _process_one_frame(self) -> None:
        frame = self._frame_source.read()
        run_detection = self._frames_processed % self._detection_interval_frames == 0
        if run_detection:
            detections = self._detector.detect(frame)
            tracked_faces = self._tracker.update(frame, detections)
            with self._lock:
                self._frames_detected += 1
        else:
            # M15 adaptive detection frequency: skip the (costly) detector
            # on this frame and only advance track geometry by motion
            # prediction. Predicted tracks are used for continuity only;
            # per-track quality/recognition/enrollment work below still
            # requires a real detection, so it is skipped this frame.
            self._tracker.predict_only(frame)
            with self._lock:
                self._frames_processed += 1
            return
        now = datetime.now(UTC)

        current_track_ids = {tracked.track_id for tracked in tracked_faces}
        for ended_track_id in self._known_track_ids - current_track_ids:
            if self._recognizer is not None:
                self._recognizer.track_ended(ended_track_id)
            if self._candidate_manager is not None:
                self._candidate_manager.track_ended(ended_track_id)
            with self._lock:
                self._latest_faces.pop(ended_track_id, None)
        self._known_track_ids = current_track_ids

        presence_candidates: list[PresenceCandidate] = []
        preview_labels: list[_PreviewLabel] = []
        for tracked in tracked_faces:
            presence, label = self._process_track(tracked, frame=frame, now=now)
            if presence is not None:
                presence_candidates.append(presence)
            preview_labels.append(label)

        self._generate_preview(frame, preview_labels)

        if self._active_user_selector is not None:
            active_decision = self._active_user_selector.update(tuple(presence_candidates), now=now)
            if self._settings_applier is not None:
                self._settings_applier.sync(active=active_decision)
            if self._preference_service is not None:
                self._preference_service.observe(active=active_decision, now=now)

        with self._lock:
            self._frames_processed += 1

    def _process_track(
        self, tracked: TrackedFace, *, frame: Frame, now: datetime
    ) -> tuple[PresenceCandidate | None, _PreviewLabel]:
        box = tracked.detection.bounding_box
        track_id = tracked.track_id
        if self._quality_evaluator is None or self._aligner is None or self._embedder is None:
            self._record_face(
                track_id, observation=_UNKNOWN_OBSERVATION, quality_score=None, now=now
            )
            return None, _PreviewLabel(box, "Unknown", PreviewCategory.UNKNOWN)
        detection = tracked.detection
        quality = self._quality_evaluator.evaluate(frame, detection)
        if not quality.accepted:
            self._record_face(
                track_id, observation=_UNKNOWN_OBSERVATION, quality_score=quality.score, now=now
            )
            return None, _PreviewLabel(box, "Unknown", PreviewCategory.UNKNOWN)
        aligned = self._aligner.align(frame, detection)
        embedding = self._embedder.generate(aligned)
        liveness_passed = True
        if self._passive_liveness_evaluator is not None:
            liveness_passed = self._passive_liveness_evaluator.evaluate(aligned).passed

        if self._recognizer is None:
            self._record_face(
                track_id, observation=_UNKNOWN_OBSERVATION, quality_score=quality.score, now=now
            )
            return None, _PreviewLabel(box, "Unknown", PreviewCategory.UNKNOWN)
        decision = self._recognizer.recognize(track_id, embedding, observed_at=now)

        candidate: Candidate | None = None
        if decision.state is RecognitionState.UNKNOWN and self._candidate_manager is not None:
            candidate = self._candidate_manager.observe_unknown(
                track_id,
                embedding,
                detection.landmarks,
                quality_score=quality.score,
                observed_at=now,
                liveness_passed=liveness_passed,
            )

        observation = self._observe_face(
            decision=decision, candidate=candidate, liveness_passed=liveness_passed
        )
        self._record_face(track_id, observation=observation, quality_score=quality.score, now=now)
        preview_label = _preview_label_from_observation(box, observation)

        if decision.state is not RecognitionState.CONFIRMED_MATCH or decision.profile_id is None:
            return None, preview_label
        if self._active_user_selector is None:
            return None, preview_label
        profile_priority = self._lookup_profile_priority(decision.profile_id)
        if profile_priority is None:
            return None, preview_label
        presence = build_presence_candidate(
            tracked,
            decision,
            frame_width=float(frame.image.shape[1]),
            frame_height=float(frame.image.shape[0]),
            profile_priority=profile_priority,
            visible_duration_seconds=tracked.age_frames * self._poll_interval_seconds,
        )
        return presence, preview_label

    def _observe_face(
        self,
        *,
        decision: RecognitionDecision,
        candidate: Candidate | None,
        liveness_passed: bool,
    ) -> _FaceObservation:
        """Determine one track's current recognition state, independent of drawing.

        Confirmed recognition always wins (a stable, temporally-confirmed
        identity). Next, a candidate this same call just promoted (M8
        automatic promotion) is shown with its brand-new permanent profile
        UUID immediately, before the separate M7 temporal-confirmation cache
        would otherwise catch up on a later frame. A liveness failure on an
        unconfirmed track is reported in its own state. Anything else --
        including AMBIGUOUS/POSSIBLE_MATCH observations that have not yet
        accumulated a candidate -- stays privacy-safe "unknown", per
        HERMES.md's unknown-person policy: no identity is ever implied
        before it is actually established.
        """

        if decision.state is RecognitionState.CONFIRMED_MATCH and decision.profile_id is not None:
            display_name = self._lookup_profile_display_name(decision.profile_id)
            if display_name is not None:
                return _FaceObservation(
                    PreviewCategory.KNOWN, decision.profile_id, None, display_name
                )
        if (
            candidate is not None
            and candidate.status is CandidateStatus.PROMOTED
            and candidate.promoted_profile_id is not None
        ):
            display_name = (
                self._lookup_profile_display_name(candidate.promoted_profile_id)
                or candidate.temporary_name
            )
            return _FaceObservation(
                PreviewCategory.KNOWN, candidate.promoted_profile_id, candidate.id, display_name
            )
        if not liveness_passed:
            return _FaceObservation(
                PreviewCategory.LIVENESS_FAILED,
                None,
                candidate.id if candidate is not None else None,
                candidate.temporary_name if candidate is not None else None,
            )
        if candidate is not None:
            return _FaceObservation(
                PreviewCategory.UNKNOWN, None, candidate.id, candidate.temporary_name
            )
        return _UNKNOWN_OBSERVATION

    def _record_face(
        self,
        track_id: int,
        *,
        observation: _FaceObservation,
        quality_score: float | None,
        now: datetime,
    ) -> None:
        snapshot = FaceSnapshot(
            track_id=track_id,
            state=observation.category,
            profile_id=observation.profile_id,
            candidate_id=observation.candidate_id,
            display_label=observation.display_name,
            quality_score=quality_score,
            last_observed_at=now,
        )
        with self._lock:
            self._latest_faces[track_id] = snapshot

    def _lookup_profile_priority(self, profile_id: UUID) -> int | None:
        if self._profiles is None:
            return None
        try:
            return self._profiles.get(profile_id).priority
        except ProfileNotFoundError:
            return None

    def _lookup_profile_display_name(self, profile_id: UUID) -> str | None:
        if self._profiles is None:
            return None
        try:
            return self._profiles.get(profile_id).display_name
        except ProfileNotFoundError:
            return None

    def _generate_preview(self, frame: Frame, labels: list[_PreviewLabel]) -> None:
        """Render at most one bounded-rate annotated JPEG snapshot per call.

        Always draws on ``frame.image.copy()`` -- the pipeline's source
        frame is never mutated -- and always replaces the single stored
        snapshot under ``self._lock`` rather than appending to anything, so
        memory use stays bounded to one encoded frame regardless of run
        length. Silently returns when preview is disabled or rate-limited.
        Any drawing or encoding error (including a non-3-channel source
        frame reaching a BGR-color ``cv2.rectangle``/``cv2.putText`` call)
        is caught and logged, never re-raised: a broken preview must never
        fail the pipeline thread, since that would stop recognition,
        enrollment, active-user selection, and settings restore too, not
        just the debug preview.
        """

        if not self._preview_enabled:
            return
        now_monotonic = time.monotonic()
        elapsed_since_last = now_monotonic - self._preview_last_generated_monotonic
        if elapsed_since_last < self._preview_min_interval_seconds:
            return
        self._preview_last_generated_monotonic = now_monotonic

        try:
            annotated: ImageArray = frame.image.copy()
            height, width = annotated.shape[:2]
            scale = 1.0
            if width > self._preview_max_width:
                scale = self._preview_max_width / width
                new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
                annotated = cast(
                    ImageArray, cv2.resize(annotated, new_size, interpolation=cv2.INTER_AREA)
                )
            for label in labels:
                _draw_preview_label(annotated, label, scale=scale)
            encode_ok, buffer = cv2.imencode(
                ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), self._preview_jpeg_quality]
            )
            if not encode_ok:
                return
            encoded = buffer.tobytes()
        except Exception as error:
            logger.warning(
                "preview generation failed; leaving previous snapshot in place",
                extra={"event_type": "PreviewGenerationFailed", "error_code": type(error).__name__},
            )
            return
        with self._lock:
            self._latest_preview_jpeg = encoded
