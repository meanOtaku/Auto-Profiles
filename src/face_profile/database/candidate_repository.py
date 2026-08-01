"""Candidate lifecycle and candidate-embedding persistence (M8).

Mirrors ``repository.py``'s conventions: write methods do not commit their
own transaction except where a method is itself a multi-statement atomic
unit (none here manage their own commit; every candidate write is meant to
be composed by a caller such as ``enrollment/manager.py`` or
``enrollment/promotion.py``). Embeddings are encrypted exactly as M6
encrypts profile embeddings — a temporary candidate is still biometric
data and gets the same at-rest protection.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np

from face_profile.database.crypto import decrypt_bytes, encrypt_bytes
from face_profile.database.keys import KeyProvider
from face_profile.database.models import (
    Candidate,
    CandidateStatus,
    StoredCandidateEmbeddingMetadata,
)
from face_profile.database.repository import Clock, ProfileRepositoryError
from face_profile.vision.embedding import EmbeddingVector, FaceEmbedding


def _default_clock() -> datetime:
    return datetime.now(UTC)


class CandidateNotFoundError(ProfileRepositoryError):
    """Raised when a referenced candidate does not exist."""


class CandidateStateError(ProfileRepositoryError):
    """Raised when a candidate lifecycle transition is not permitted."""


def _row_to_candidate(row: sqlite3.Row) -> Candidate:
    return Candidate(
        id=UUID(row["id"]),
        temporary_name=row["temporary_name"],
        status=CandidateStatus(row["status"]),
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        sample_count=row["sample_count"],
        aggregate_quality=row["aggregate_quality"],
        review_status=row["review_status"],
        reviewed_by=row["reviewed_by"],
        reviewed_at=(datetime.fromisoformat(row["reviewed_at"]) if row["reviewed_at"] else None),
        retention_expires_at=(
            datetime.fromisoformat(row["retention_expires_at"])
            if row["retention_expires_at"]
            else None
        ),
        promoted_profile_id=(
            UUID(row["promoted_profile_id"]) if row["promoted_profile_id"] else None
        ),
        metadata=json.loads(row["metadata_json"]),
    )


class CandidateRepository:
    """Durable candidate lifecycle, sample storage, and review-state transitions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        key_provider: KeyProvider,
        clock: Clock | None = None,
    ) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._key_provider = key_provider
        self._clock = clock or _default_clock

    def create_candidate(
        self,
        *,
        first_seen_at: datetime,
        name_prefix: str = "Unknown",
        metadata: Mapping[str, object] | None = None,
    ) -> Candidate:
        """Create a new COLLECTING candidate with a predictable temporary name.

        HERMES's ``CREATED`` state is momentary: a candidate becomes
        observable only once it has at least one sample, so this method
        inserts directly into ``COLLECTING`` rather than persisting a
        separate zero-duration row.
        """

        candidate_id = uuid4()
        temporary_name = self._next_temporary_name(name_prefix)
        timestamp = first_seen_at.isoformat()
        self._connection.execute(
            "INSERT INTO candidates (id, temporary_name, status, first_seen_at, "
            "last_seen_at, sample_count, aggregate_quality, review_status, "
            "reviewed_by, reviewed_at, retention_expires_at, promoted_profile_id, "
            "metadata_json) VALUES (?, ?, ?, ?, ?, 0, 0.0, 'pending', NULL, NULL, "
            "NULL, NULL, ?)",
            (
                str(candidate_id),
                temporary_name,
                CandidateStatus.COLLECTING.value,
                timestamp,
                timestamp,
                json.dumps(dict(metadata or {})),
            ),
        )
        return self.get(candidate_id)

    def _next_temporary_name(self, prefix: str) -> str:
        row = self._connection.execute(
            "SELECT next_value FROM candidate_name_sequence WHERE id = 1"
        ).fetchone()
        next_value = int(row[0])
        self._connection.execute(
            "UPDATE candidate_name_sequence SET next_value = next_value + 1 WHERE id = 1"
        )
        return f"{prefix}-{next_value:06d}"

    def get(self, candidate_id: UUID) -> Candidate:
        """Return one candidate or raise :class:`CandidateNotFoundError`."""

        row = self._connection.execute(
            "SELECT * FROM candidates WHERE id = ?", (str(candidate_id),)
        ).fetchone()
        if row is None:
            raise CandidateNotFoundError(str(candidate_id))
        return _row_to_candidate(row)

    def list(
        self,
        *,
        status: CandidateStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Candidate, ...]:
        """Return candidates ordered by first-seen time, optionally filtered."""

        if limit < 1 or limit > 1000:
            raise ProfileRepositoryError("limit must be between 1 and 1000")
        if status is None:
            rows = self._connection.execute(
                "SELECT * FROM candidates ORDER BY first_seen_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM candidates WHERE status = ? ORDER BY first_seen_at ASC "
                "LIMIT ? OFFSET ?",
                (status.value, limit, offset),
            ).fetchall()
        return tuple(_row_to_candidate(row) for row in rows)

    def add_sample(
        self,
        candidate_id: UUID,
        embedding: FaceEmbedding,
        *,
        quality_score: float,
        observed_at: datetime,
        source_camera_id: str | None = None,
        source_track_id: int | None = None,
        max_samples: int = 50,
    ) -> UUID:
        """Encrypt and append one sample, updating rolling candidate metadata."""

        candidate = self.get(candidate_id)
        if candidate.status is not CandidateStatus.COLLECTING:
            raise CandidateStateError(
                f"cannot add a sample to a candidate in status {candidate.status}"
            )
        if candidate.sample_count >= max_samples:
            raise CandidateStateError("maximum candidate sample count reached")
        key = self._key_provider.get_key()
        encrypted = encrypt_bytes(
            key, embedding.vector.tobytes(), associated_data=str(candidate_id).encode()
        )
        embedding_id = uuid4()
        timestamp = observed_at.isoformat()
        self._connection.execute(
            "INSERT INTO candidate_embeddings (id, candidate_id, model_name, "
            "model_version, model_checksum, embedding_dimension, numeric_dtype, "
            "encrypted_vector, quality_score, source_camera_id, source_track_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(embedding_id),
                str(candidate_id),
                embedding.model_name,
                embedding.model_version,
                embedding.model_checksum,
                embedding.dimension,
                embedding.numeric_dtype,
                encrypted,
                quality_score,
                source_camera_id,
                source_track_id,
                timestamp,
            ),
        )
        new_sample_count = candidate.sample_count + 1
        new_aggregate_quality = (
            candidate.aggregate_quality * candidate.sample_count + quality_score
        ) / new_sample_count
        self._connection.execute(
            "UPDATE candidates SET sample_count = ?, aggregate_quality = ?, "
            "last_seen_at = ? WHERE id = ?",
            (new_sample_count, new_aggregate_quality, timestamp, str(candidate_id)),
        )
        return embedding_id

    def list_embeddings(self, candidate_id: UUID) -> tuple[StoredCandidateEmbeddingMetadata, ...]:
        """Return candidate embedding metadata, excluding vector bytes."""

        rows = self._connection.execute(
            "SELECT id, candidate_id, model_name, model_version, model_checksum, "
            "embedding_dimension, numeric_dtype, quality_score, source_camera_id, "
            "source_track_id, created_at FROM candidate_embeddings "
            "WHERE candidate_id = ? ORDER BY created_at ASC",
            (str(candidate_id),),
        ).fetchall()
        return tuple(
            StoredCandidateEmbeddingMetadata(
                id=UUID(row["id"]),
                candidate_id=UUID(row["candidate_id"]),
                model_name=row["model_name"],
                model_version=row["model_version"],
                model_checksum=row["model_checksum"],
                embedding_dimension=row["embedding_dimension"],
                numeric_dtype=row["numeric_dtype"],
                quality_score=row["quality_score"],
                source_camera_id=row["source_camera_id"],
                source_track_id=row["source_track_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def get_embedding_vector(self, embedding_id: UUID) -> FaceEmbedding:
        """Decrypt and return one candidate embedding's full vector on demand."""

        row = self._connection.execute(
            "SELECT * FROM candidate_embeddings WHERE id = ?", (str(embedding_id),)
        ).fetchone()
        if row is None:
            raise CandidateNotFoundError(str(embedding_id))
        key = self._key_provider.get_key()
        raw = decrypt_bytes(
            key, row["encrypted_vector"], associated_data=row["candidate_id"].encode()
        )
        vector: EmbeddingVector = np.frombuffer(raw, dtype=np.float32).copy()
        return FaceEmbedding(
            vector=vector,
            model_name=row["model_name"],
            model_version=row["model_version"],
            model_checksum=row["model_checksum"],
            dimension=row["embedding_dimension"],
            numeric_dtype=row["numeric_dtype"],
        )

    def mark_ready_for_review(self, candidate_id: UUID) -> Candidate:
        """Transition COLLECTING to READY_FOR_REVIEW once qualification passes."""

        candidate = self.get(candidate_id)
        if candidate.status is not CandidateStatus.COLLECTING:
            raise CandidateStateError(f"cannot ready a candidate in status {candidate.status}")
        self._connection.execute(
            "UPDATE candidates SET status = ?, review_status = 'pending' WHERE id = ?",
            (CandidateStatus.READY_FOR_REVIEW.value, str(candidate_id)),
        )
        return self.get(candidate_id)

    def reject(
        self, candidate_id: UUID, *, reason: str, reviewed_by: str | None = None
    ) -> Candidate:
        """Reject a candidate and immediately delete its biometric samples."""

        candidate = self.get(candidate_id)
        if candidate.status not in (CandidateStatus.COLLECTING, CandidateStatus.READY_FOR_REVIEW):
            raise CandidateStateError(f"cannot reject a candidate in status {candidate.status}")
        now = self._clock().isoformat()
        self._connection.execute(
            "DELETE FROM candidate_embeddings WHERE candidate_id = ?", (str(candidate_id),)
        )
        self._connection.execute(
            "UPDATE candidates SET status = ?, review_status = ?, reviewed_by = ?, "
            "reviewed_at = ? WHERE id = ?",
            (CandidateStatus.REJECTED.value, reason, reviewed_by, now, str(candidate_id)),
        )
        return self.get(candidate_id)

    def expire(self, candidate_id: UUID) -> Candidate:
        """Expire a candidate and immediately delete its biometric samples.

        Per HERMES.md's unknown-person policy, an expired candidate must
        never create a permanent profile; deleting its samples immediately
        keeps unqualified biometric data from lingering.
        """

        candidate = self.get(candidate_id)
        if candidate.status is not CandidateStatus.COLLECTING:
            raise CandidateStateError(f"cannot expire a candidate in status {candidate.status}")
        self._connection.execute(
            "DELETE FROM candidate_embeddings WHERE candidate_id = ?", (str(candidate_id),)
        )
        self._connection.execute(
            "UPDATE candidates SET status = ? WHERE id = ?",
            (CandidateStatus.EXPIRED.value, str(candidate_id)),
        )
        return self.get(candidate_id)

    def expire_stale(self, *, now: datetime, expiry_seconds: float) -> tuple[UUID, ...]:
        """Expire every COLLECTING candidate whose last sample is too old."""

        rows = self._connection.execute(
            "SELECT id, last_seen_at FROM candidates WHERE status = ?",
            (CandidateStatus.COLLECTING.value,),
        ).fetchall()
        expired: list[UUID] = []
        for row in rows:
            last_seen_at = datetime.fromisoformat(row["last_seen_at"])
            if (now - last_seen_at).total_seconds() > expiry_seconds:
                candidate_id = UUID(row["id"])
                self.expire(candidate_id)
                expired.append(candidate_id)
        return tuple(expired)

    def mark_promoted(self, candidate_id: UUID, *, profile_id: UUID, reviewed_by: str) -> Candidate:
        """Transition READY_FOR_REVIEW to PROMOTED and delete duplicate samples.

        The candidate's embeddings must already have been copied to the
        profile by the caller before this is called; this method deletes
        the now-redundant candidate-side copies within the same transaction
        so a promoted person's biometric data is not stored twice.
        """

        candidate = self.get(candidate_id)
        if candidate.status is not CandidateStatus.READY_FOR_REVIEW:
            raise CandidateStateError(f"cannot promote a candidate in status {candidate.status}")
        now = self._clock().isoformat()
        self._connection.execute(
            "DELETE FROM candidate_embeddings WHERE candidate_id = ?", (str(candidate_id),)
        )
        self._connection.execute(
            "UPDATE candidates SET status = ?, review_status = 'approved', "
            "reviewed_by = ?, reviewed_at = ?, promoted_profile_id = ? WHERE id = ?",
            (
                CandidateStatus.PROMOTED.value,
                reviewed_by,
                now,
                str(profile_id),
                str(candidate_id),
            ),
        )
        return self.get(candidate_id)

    def purge_terminal(self, *, now: datetime, retention_days: int) -> int:
        """Permanently remove old EXPIRED/REJECTED candidate rows.

        PROMOTED candidates are intentionally excluded: they are the audit
        trail linking a profile to its originating candidate and are kept
        until a dedicated audit-retention policy is defined.
        """

        cutoff = now.isoformat()
        cursor = self._connection.execute(
            "DELETE FROM candidates WHERE status IN (?, ?) AND datetime(last_seen_at, ?) <= ?",
            (
                CandidateStatus.EXPIRED.value,
                CandidateStatus.REJECTED.value,
                f"+{retention_days} days",
                cutoff,
            ),
        )
        return cursor.rowcount
