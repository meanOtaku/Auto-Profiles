"""Build M10 presence candidates from M3/M6/M7 outputs.

A thin adapter: it performs no scoring or state decisions itself
(``active_user.py`` owns that), only geometric normalization and assembly.
"""

from __future__ import annotations

from face_profile.presence.active_user import PresenceCandidate
from face_profile.recognition.decision import RecognitionDecision, RecognitionState
from face_profile.vision.tracking import TrackedFace


def build_presence_candidate(
    tracked: TrackedFace,
    decision: RecognitionDecision,
    *,
    frame_width: float,
    frame_height: float,
    profile_priority: int,
    visible_duration_seconds: float,
) -> PresenceCandidate | None:
    """Return a presence candidate only for a track M7 has confirmed.

    Unknown, ambiguous, or merely possible-match tracks return ``None``:
    active-user selection only ever targets an identity M7 has actually
    confirmed, since personalization requires a real profile ID.
    """

    if decision.state is not RecognitionState.CONFIRMED_MATCH or decision.profile_id is None:
        return None

    box = tracked.detection.bounding_box
    frame_area = frame_width * frame_height
    face_area = box.width * box.height
    normalized_face_size = min(1.0, face_area / frame_area) if frame_area > 0 else 0.0

    box_centre_x = box.x + box.width / 2.0
    box_centre_y = box.y + box.height / 2.0
    frame_centre_x = frame_width / 2.0
    frame_centre_y = frame_height / 2.0
    max_distance = ((frame_centre_x**2) + (frame_centre_y**2)) ** 0.5
    distance = ((box_centre_x - frame_centre_x) ** 2 + (box_centre_y - frame_centre_y) ** 2) ** 0.5
    centre_proximity = 1.0 - min(1.0, distance / max_distance) if max_distance > 0 else 1.0

    confidence = decision.similarity if decision.similarity is not None else 0.0
    confidence = max(0.0, min(1.0, confidence))

    return PresenceCandidate(
        track_id=tracked.track_id,
        profile_id=decision.profile_id,
        normalized_face_size=normalized_face_size,
        centre_proximity=centre_proximity,
        visible_duration_seconds=max(0.0, visible_duration_seconds),
        recognition_confidence=confidence,
        profile_priority=profile_priority,
    )
