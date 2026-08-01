"""Durable profile-domain value objects for the M6 persistence boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class ProfileStatus(StrEnum):
    """Lifecycle status for one persisted profile."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISABLED = "disabled"
    MERGED = "merged"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class Profile:
    """A persisted, uniquely identified person profile."""

    id: UUID
    display_name: str
    status: ProfileStatus
    priority: int
    is_owner: bool
    metadata: Mapping[str, object]
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None
    merged_into_profile_id: UUID | None
    enrollment_review_status: str
    retention_expires_at: datetime | None
    optimistic_version: int


@dataclass(frozen=True, slots=True)
class StoredEmbeddingMetadata:
    """Non-secret metadata for one persisted embedding (vector excluded)."""

    id: UUID
    profile_id: UUID
    model_name: str
    model_version: str
    model_checksum: str
    embedding_dimension: int
    numeric_dtype: str
    quality_score: float
    is_representative: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecognitionEventRecord:
    """One durable, append-only recognition-domain event."""

    id: UUID
    event_type: str
    profile_id: UUID | None
    candidate_id: UUID | None
    track_id: int | None
    similarity: float | None
    camera_id: str | None
    occurred_at: datetime
    sequence: int
    correlation_id: str | None
    metadata: Mapping[str, object]
