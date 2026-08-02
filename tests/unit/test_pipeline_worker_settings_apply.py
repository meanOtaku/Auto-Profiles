"""RED-first test for item 7: PipelineWorker must actually invoke the
active-profile settings applier once per processed frame with the M10
active-user decision, wiring HERMES.md's "Select active user" -> "Load and
apply active profile settings" pipeline step end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from face_profile.api.worker import PipelineWorker
from face_profile.camera import Frame
from face_profile.presence.active_user import ActiveUserDecision, ActiveUserState


class FakeFrameSource:
    def __init__(self) -> None:
        self.sequence = 0

    def open(self) -> None:
        pass

    def read(self) -> Frame:
        self.sequence += 1
        return Frame(
            source_id="fake",
            sequence=self.sequence,
            captured_at=datetime.now(UTC),
            image=np.zeros((4, 4, 3), dtype=np.uint8),
        )

    def close(self) -> None:
        pass


class FakeDetector:
    def detect(self, frame: Frame) -> tuple:
        return ()


class FakeTracker:
    def update(self, frame: Frame, detections: tuple) -> tuple:
        return ()

    def predict_only(self, frame: Frame) -> tuple:
        return ()

    def active_tracks(self) -> tuple:
        return ()


class FakeActiveUserSelector:
    def __init__(self, decision: ActiveUserDecision) -> None:
        self._decision = decision
        self.update_calls = 0

    def update(self, candidates: tuple, *, now: datetime) -> ActiveUserDecision:
        self.update_calls += 1
        return self._decision


class FakeSettingsApplier:
    def __init__(self) -> None:
        self.sync_calls: list[ActiveUserDecision] = []

    def sync(self, *, active: ActiveUserDecision) -> None:
        self.sync_calls.append(active)


def test_worker_calls_settings_applier_with_active_decision_each_frame() -> None:
    decision = ActiveUserDecision(
        state=ActiveUserState.ACTIVE,
        profile_id=uuid4(),
        score=1.0,
        active_since=datetime.now(UTC),
    )
    selector = FakeActiveUserSelector(decision)
    applier = FakeSettingsApplier()

    worker = PipelineWorker(
        frame_source=FakeFrameSource(),
        detector=FakeDetector(),
        tracker=FakeTracker(),
        active_user_selector=selector,
        settings_applier=applier,
    )

    worker._process_one_frame()
    worker._process_one_frame()

    assert applier.sync_calls == [decision, decision]


def test_worker_skips_settings_applier_without_active_user_selector() -> None:
    applier = FakeSettingsApplier()

    worker = PipelineWorker(
        frame_source=FakeFrameSource(),
        detector=FakeDetector(),
        tracker=FakeTracker(),
        active_user_selector=None,
        settings_applier=applier,
    )

    worker._process_one_frame()

    assert applier.sync_calls == []
