"""Pydantic request/response schemas for the M12 REST API.

Domain repositories return typed dataclasses (``database.models``); these
schemas are the API boundary's own translation layer, per
CODING_STANDARDS.md's "Pydantic for API schemas" rule. Vectors and other
biometric payloads are never included.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from face_profile.api.worker import FaceSnapshot
from face_profile.database.models import Candidate, Profile


class ApiModel(BaseModel):
    """Strict base: reject unknown fields, forbid silent coercion drift."""

    model_config = ConfigDict(extra="forbid")


class ProfileResponse(ApiModel):
    id: UUID
    display_name: str
    status: str
    priority: int
    is_owner: bool
    metadata: dict[str, object]
    created_at: str
    updated_at: str
    last_seen_at: str | None
    enrollment_review_status: str
    optimistic_version: int

    @classmethod
    def from_domain(cls, profile: Profile) -> ProfileResponse:
        return cls(
            id=profile.id,
            display_name=profile.display_name,
            status=profile.status.value,
            priority=profile.priority,
            is_owner=profile.is_owner,
            metadata=dict(profile.metadata),
            created_at=profile.created_at.isoformat(),
            updated_at=profile.updated_at.isoformat(),
            last_seen_at=profile.last_seen_at.isoformat() if profile.last_seen_at else None,
            enrollment_review_status=profile.enrollment_review_status,
            optimistic_version=profile.optimistic_version,
        )


class ProfileListResponse(ApiModel):
    items: list[ProfileResponse]
    limit: int
    offset: int


class ProfileCreateRequest(ApiModel):
    display_name: str = Field(min_length=1, max_length=200)
    is_owner: bool = False
    priority: int = Field(default=0, ge=0, le=1000)


class ProfilePatchRequest(ApiModel):
    expected_version: int
    display_name: str | None = None
    priority: int | None = None
    status: str | None = None
    metadata: dict[str, object] | None = None


class ProfileMergeRequest(ApiModel):
    target_id: UUID
    source_expected_version: int
    target_expected_version: int


class ProfileExportRequest(ApiModel):
    profile_id: UUID


class ProfileExportResponse(ApiModel):
    blob_base64: str


class ProfileImportRequest(ApiModel):
    blob_base64: str


class CandidateResponse(ApiModel):
    id: UUID
    temporary_name: str
    status: str
    first_seen_at: str
    last_seen_at: str
    sample_count: int
    aggregate_quality: float
    review_status: str
    reviewed_by: str | None
    reviewed_at: str | None
    promoted_profile_id: UUID | None

    @classmethod
    def from_domain(cls, candidate: Candidate) -> CandidateResponse:
        return cls(
            id=candidate.id,
            temporary_name=candidate.temporary_name,
            status=candidate.status.value,
            first_seen_at=candidate.first_seen_at.isoformat(),
            last_seen_at=candidate.last_seen_at.isoformat(),
            sample_count=candidate.sample_count,
            aggregate_quality=candidate.aggregate_quality,
            review_status=candidate.review_status,
            reviewed_by=candidate.reviewed_by,
            reviewed_at=candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
            promoted_profile_id=candidate.promoted_profile_id,
        )


class CandidateListResponse(ApiModel):
    items: list[CandidateResponse]
    limit: int
    offset: int


class CandidateApproveRequest(ApiModel):
    reviewed_by: str = Field(min_length=1, max_length=200)


class CandidateRejectRequest(ApiModel):
    reason: str = Field(min_length=1, max_length=200)
    reviewed_by: str | None = None


class DeviceSettingsResponse(ApiModel):
    volume: int
    brightness: int


class ProfileSettingsRequest(ApiModel):
    volume: int = Field(ge=0, le=100)
    brightness: int = Field(ge=0, le=100)


class EventResponse(ApiModel):
    id: UUID
    event_type: str
    profile_id: UUID | None
    candidate_id: UUID | None
    track_id: int | None
    similarity: float | None
    camera_id: str | None
    occurred_at: str
    sequence: int
    correlation_id: str | None


class EventListResponse(ApiModel):
    items: list[EventResponse]
    limit: int


class HealthResponse(ApiModel):
    status: str


class StatusResponse(ApiModel):
    service_state: str
    worker_state: str
    frames_processed: int
    frames_detected: int
    worker_last_error: str | None
    database_enabled: bool
    camera_enabled: bool


class MetricsResponse(ApiModel):
    """M15 metrics export: a minimal JSON snapshot, not a Prometheus endpoint.

    Kept intentionally small and privacy-safe: counts only, no biometric
    payloads, no per-person identifiers beyond aggregate counts.
    """

    worker_state: str
    frames_processed: int
    frames_detected: int
    detection_skip_ratio: float
    active_profile_count: int
    collecting_candidate_count: int
    ready_for_review_candidate_count: int


class ErrorResponse(ApiModel):
    error_code: str
    message: str


class FaceResponse(ApiModel):
    """Privacy-safe current-face metadata: never images, crops, or embeddings."""

    track_id: int
    state: str
    profile_id: UUID | None
    candidate_id: UUID | None
    display_label: str | None
    quality_score: float | None
    last_observed_at: str

    @classmethod
    def from_domain(cls, face: FaceSnapshot) -> FaceResponse:
        return cls(
            track_id=face.track_id,
            state=face.state.value,
            profile_id=face.profile_id,
            candidate_id=face.candidate_id,
            display_label=face.display_label,
            quality_score=face.quality_score,
            last_observed_at=face.last_observed_at.isoformat(),
        )


class FaceListResponse(ApiModel):
    items: list[FaceResponse]


class CapabilitiesResponse(ApiModel):
    """Safe booleans/statuses only -- no secrets, no biometric configuration values."""

    api_auth_required: bool
    ui_enabled: bool
    webcam_preview_enabled: bool
    camera_enabled: bool
    database_enabled: bool
    enrollment_enabled: bool
    settings_backend: str
    worker_state: str
