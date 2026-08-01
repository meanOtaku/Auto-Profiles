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
from uuid import UUID

from face_profile.camera import EndOfFrames, Frame, FrameSourceLifecycle, TemporaryFrameSourceError
from face_profile.database.repository import ProfileNotFoundError, ProfileRepository
from face_profile.enrollment.manager import CandidateManager
from face_profile.presence.active_user import ActiveUserSelector, PresenceCandidate
from face_profile.presence.builder import build_presence_candidate
from face_profile.recognition.decision import RecognitionState
from face_profile.recognition.recognizer import KnownPersonRecognizer
from face_profile.settings.last_used_service import LastUsedPreferenceService
from face_profile.vision.alignment import FaceAligner
from face_profile.vision.detection import FaceDetector
from face_profile.vision.embedding import EmbeddingGenerator
from face_profile.vision.quality import QualityEvaluator
from face_profile.vision.tracking import FaceTracker, TrackedFace

logger = logging.getLogger("face_profile.api.worker")


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
    last_error: str | None


class PipelineWorker:
    """Runs the detect->track->quality->align->embed->recognize->... loop."""

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
        preference_service: LastUsedPreferenceService | None = None,
        profiles: ProfileRepository | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self._frame_source = frame_source
        self._detector = detector
        self._tracker = tracker
        self._quality_evaluator = quality_evaluator
        self._aligner = aligner
        self._embedder = embedder
        self._recognizer = recognizer
        self._candidate_manager = candidate_manager
        self._active_user_selector = active_user_selector
        self._preference_service = preference_service
        self._profiles = profiles
        self._poll_interval_seconds = poll_interval_seconds

        self._state = WorkerState.STOPPED
        self._frames_processed = 0
        self._last_error: str | None = None
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._known_track_ids: set[int] = set()
        self._lock = threading.Lock()

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
        self._pause_event.set()
        with self._lock:
            if self._state is WorkerState.RUNNING:
                self._state = WorkerState.PAUSED

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

    def health(self) -> WorkerHealth:
        with self._lock:
            return WorkerHealth(
                state=self._state,
                frames_processed=self._frames_processed,
                last_error=self._last_error,
            )

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
        detections = self._detector.detect(frame)
        tracked_faces = self._tracker.update(frame, detections)
        now = datetime.now(UTC)

        current_track_ids = {tracked.track_id for tracked in tracked_faces}
        for ended_track_id in self._known_track_ids - current_track_ids:
            if self._recognizer is not None:
                self._recognizer.track_ended(ended_track_id)
            if self._candidate_manager is not None:
                self._candidate_manager.track_ended(ended_track_id)
        self._known_track_ids = current_track_ids

        presence_candidates: list[PresenceCandidate] = []
        for tracked in tracked_faces:
            candidate = self._process_track(tracked, frame=frame, now=now)
            if candidate is not None:
                presence_candidates.append(candidate)

        if self._active_user_selector is not None:
            active_decision = self._active_user_selector.update(tuple(presence_candidates), now=now)
            if self._preference_service is not None:
                self._preference_service.observe(active=active_decision, now=now)

        with self._lock:
            self._frames_processed += 1

    def _process_track(
        self, tracked: TrackedFace, *, frame: Frame, now: datetime
    ) -> PresenceCandidate | None:
        if self._quality_evaluator is None or self._aligner is None or self._embedder is None:
            return None
        detection = tracked.detection
        quality = self._quality_evaluator.evaluate(frame, detection)
        if not quality.accepted:
            return None
        aligned = self._aligner.align(frame, detection)
        embedding = self._embedder.generate(aligned)

        if self._recognizer is None:
            return None
        decision = self._recognizer.recognize(tracked.track_id, embedding, observed_at=now)

        if decision.state is RecognitionState.UNKNOWN and self._candidate_manager is not None:
            self._candidate_manager.observe_unknown(
                tracked.track_id,
                embedding,
                detection.landmarks,
                quality_score=quality.score,
                observed_at=now,
            )

        if decision.state is not RecognitionState.CONFIRMED_MATCH or decision.profile_id is None:
            return None
        if self._active_user_selector is None:
            return None
        profile_priority = self._lookup_profile_priority(decision.profile_id)
        if profile_priority is None:
            return None
        return build_presence_candidate(
            tracked,
            decision,
            frame_width=float(frame.image.shape[1]),
            frame_height=float(frame.image.shape[0]),
            profile_priority=profile_priority,
            visible_duration_seconds=tracked.age_frames * self._poll_interval_seconds,
        )

    def _lookup_profile_priority(self, profile_id: UUID) -> int | None:
        if self._profiles is None:
            return None
        try:
            return self._profiles.get(profile_id).priority
        except ProfileNotFoundError:
            return None
