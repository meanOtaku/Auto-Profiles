"""Profile, embedding, settings, and event persistence repositories.

M6 owns durable storage only. It does not decide identity (M7), enrollment
policy (M8), active-user selection (M10), or apply settings to a real
device (M9/M11). Domain services must depend on these repository classes
rather than on SQL or the connection directly, per CODING_STANDARDS.md.

Repository write methods do not commit their own transaction (an M8
correction: the original M6 per-call auto-commit made these methods
unusable as composable steps in a larger atomic operation, such as M8's
candidate-promotion transaction). Callers own commit/rollback boundaries,
typically via :meth:`ProfileDatabase.commit`/:meth:`ProfileDatabase.rollback`
around one logical operation. ``merge()`` and ``import_profile()`` are
themselves multi-statement atomic units, so they still manage their own
commit/rollback internally.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np

from face_profile.database.crypto import decrypt_bytes, encrypt_bytes
from face_profile.database.keys import KeyProvider
from face_profile.database.models import (
    Profile,
    ProfileStatus,
    RecognitionEventRecord,
    StoredEmbeddingMetadata,
)
from face_profile.settings import DeviceSettings
from face_profile.vision.embedding import EmbeddingVector, FaceEmbedding

Clock = Callable[[], datetime]

_EXPORT_ASSOCIATED_DATA = b"face-profile-export-v1"


class ProfileRepositoryError(RuntimeError):
    """Raised when a profile-repository operation cannot complete safely."""


class ProfileNotFoundError(ProfileRepositoryError):
    """Raised when a referenced profile does not exist."""


class EmbeddingNotFoundError(ProfileRepositoryError):
    """Raised when a referenced embedding does not exist."""


class ConcurrencyConflictError(ProfileRepositoryError):
    """Raised when an update's expected optimistic version is stale."""


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _row_to_profile(row: sqlite3.Row) -> Profile:
    return Profile(
        id=UUID(row["id"]),
        display_name=row["display_name"],
        status=ProfileStatus(row["status"]),
        priority=row["priority"],
        is_owner=bool(row["is_owner"]),
        metadata=json.loads(row["metadata_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_seen_at=(datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None),
        merged_into_profile_id=(
            UUID(row["merged_into_profile_id"]) if row["merged_into_profile_id"] else None
        ),
        enrollment_review_status=row["enrollment_review_status"],
        retention_expires_at=(
            datetime.fromisoformat(row["retention_expires_at"])
            if row["retention_expires_at"]
            else None
        ),
        optimistic_version=row["optimistic_version"],
    )


class ProfileRepository:
    """Durable CRUD, embedding storage, merge, and export/import for profiles."""

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

    def create(
        self,
        *,
        display_name: str,
        is_owner: bool = False,
        priority: int = 0,
        status: ProfileStatus = ProfileStatus.ACTIVE,
        metadata: Mapping[str, object] | None = None,
    ) -> Profile:
        """Create a new profile with a fresh UUID and version 1."""

        if not display_name.strip():
            raise ProfileRepositoryError("display_name must not be empty")
        profile_id = uuid4()
        now = self._clock().isoformat()
        self._connection.execute(
            "INSERT INTO profiles (id, display_name, status, priority, is_owner, "
            "metadata_json, created_at, updated_at, last_seen_at, "
            "merged_into_profile_id, enrollment_review_status, retention_expires_at, "
            "optimistic_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, 1)",
            (
                str(profile_id),
                display_name,
                status.value,
                priority,
                int(is_owner),
                json.dumps(dict(metadata or {})),
                now,
                now,
                "not_applicable",
            ),
        )
        return self.get(profile_id)

    def get(self, profile_id: UUID) -> Profile:
        """Return one profile or raise :class:`ProfileNotFoundError`."""

        row = self._connection.execute(
            "SELECT * FROM profiles WHERE id = ?", (str(profile_id),)
        ).fetchone()
        if row is None:
            raise ProfileNotFoundError(str(profile_id))
        return _row_to_profile(row)

    def list(
        self,
        *,
        status: ProfileStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Profile, ...]:
        """Return profiles ordered by creation time, optionally filtered by status."""

        if limit < 1 or limit > 1000:
            raise ProfileRepositoryError("limit must be between 1 and 1000")
        if offset < 0:
            raise ProfileRepositoryError("offset must not be negative")
        if status is None:
            rows = self._connection.execute(
                "SELECT * FROM profiles ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM profiles WHERE status = ? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (status.value, limit, offset),
            ).fetchall()
        return tuple(_row_to_profile(row) for row in rows)

    def update(
        self,
        profile_id: UUID,
        *,
        expected_version: int,
        display_name: str | None = None,
        priority: int | None = None,
        status: ProfileStatus | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Profile:
        """Apply an optimistic-concurrency-checked partial update."""

        current = self.get(profile_id)
        if current.optimistic_version != expected_version:
            raise ConcurrencyConflictError(
                f"expected version {expected_version}, found {current.optimistic_version}"
            )
        now = self._clock().isoformat()
        cursor = self._connection.execute(
            "UPDATE profiles SET display_name = ?, priority = ?, status = ?, "
            "metadata_json = ?, updated_at = ?, optimistic_version = optimistic_version + 1 "
            "WHERE id = ? AND optimistic_version = ?",
            (
                display_name if display_name is not None else current.display_name,
                priority if priority is not None else current.priority,
                status.value if status is not None else current.status.value,
                json.dumps(dict(metadata))
                if metadata is not None
                else json.dumps(dict(current.metadata)),
                now,
                str(profile_id),
                expected_version,
            ),
        )
        if cursor.rowcount == 0:
            raise ConcurrencyConflictError("profile was modified concurrently")
        return self.get(profile_id)

    def touch_last_seen(self, profile_id: UUID, *, seen_at: datetime | None = None) -> None:
        """Record presence without participating in optimistic concurrency."""

        timestamp = (seen_at or self._clock()).isoformat()
        cursor = self._connection.execute(
            "UPDATE profiles SET last_seen_at = ? WHERE id = ?",
            (timestamp, str(profile_id)),
        )
        if cursor.rowcount == 0:
            raise ProfileNotFoundError(str(profile_id))

    def disable(self, profile_id: UUID, *, expected_version: int) -> Profile:
        """Set a profile's status to disabled."""

        return self.update(
            profile_id, expected_version=expected_version, status=ProfileStatus.DISABLED
        )

    def soft_delete(
        self, profile_id: UUID, *, expected_version: int, retention_days: int
    ) -> Profile:
        """Mark a profile deleted and set its retention expiry."""

        if retention_days < 1:
            raise ProfileRepositoryError("retention_days must be positive")
        current = self.get(profile_id)
        if current.optimistic_version != expected_version:
            raise ConcurrencyConflictError(
                f"expected version {expected_version}, found {current.optimistic_version}"
            )
        now = self._clock()
        expires_at = now.timestamp() + retention_days * 86400
        cursor = self._connection.execute(
            "UPDATE profiles SET status = ?, retention_expires_at = ?, updated_at = ?, "
            "optimistic_version = optimistic_version + 1 WHERE id = ? AND optimistic_version = ?",
            (
                ProfileStatus.DELETED.value,
                datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
                now.isoformat(),
                str(profile_id),
                expected_version,
            ),
        )
        if cursor.rowcount == 0:
            raise ConcurrencyConflictError("profile was modified concurrently")
        return self.get(profile_id)

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Permanently remove deleted profiles whose retention has expired.

        Cascading foreign keys remove the profile's embeddings and settings.
        This is a normal SQL DELETE, not a secure-erase of underlying disk
        pages; documented as a known limitation until a secure-wipe or
        VACUUM-based retention job is added.
        """

        cutoff = (now or self._clock()).isoformat()
        cursor = self._connection.execute(
            "DELETE FROM profiles WHERE status = ? AND retention_expires_at IS NOT NULL "
            "AND retention_expires_at <= ?",
            (ProfileStatus.DELETED.value, cutoff),
        )
        return cursor.rowcount

    def merge(
        self,
        *,
        source_id: UUID,
        target_id: UUID,
        expected_source_version: int,
        expected_target_version: int,
    ) -> Profile:
        """Atomically reassign a source profile's embeddings to the target.

        This is the mechanical repository operation only. Whether a merge is
        appropriate (deduplication policy, identity confidence) is an M7/M8
        decision made before calling this method. Unlike other write
        methods, ``merge`` is itself a multi-statement atomic unit, so it
        commits on success and rolls back on failure rather than leaving
        that to the caller.
        """

        if source_id == target_id:
            raise ProfileRepositoryError("cannot merge a profile into itself")
        source = self.get(source_id)
        target = self.get(target_id)
        if source.optimistic_version != expected_source_version:
            raise ConcurrencyConflictError("source profile was modified concurrently")
        if target.optimistic_version != expected_target_version:
            raise ConcurrencyConflictError("target profile was modified concurrently")
        now = self._clock().isoformat()
        try:
            self._reencrypt_embeddings_for_merge(source_id=source_id, target_id=target_id)
            source_cursor = self._connection.execute(
                "UPDATE profiles SET status = ?, merged_into_profile_id = ?, updated_at = ?, "
                "optimistic_version = optimistic_version + 1 "
                "WHERE id = ? AND optimistic_version = ?",
                (
                    ProfileStatus.MERGED.value,
                    str(target_id),
                    now,
                    str(source_id),
                    expected_source_version,
                ),
            )
            target_cursor = self._connection.execute(
                "UPDATE profiles SET updated_at = ?, optimistic_version = optimistic_version + 1 "
                "WHERE id = ? AND optimistic_version = ?",
                (now, str(target_id), expected_target_version),
            )
            if source_cursor.rowcount == 0 or target_cursor.rowcount == 0:
                raise ConcurrencyConflictError("a profile was modified concurrently")
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return self.get(target_id)

    def _reencrypt_embeddings_for_merge(self, *, source_id: UUID, target_id: UUID) -> None:
        """Move embeddings to the target profile, re-keying their AEAD binding.

        Each embedding's ciphertext associated data is bound to its owning
        profile ID (see :meth:`add_embedding`), so reassigning ``profile_id``
        alone would make every moved embedding fail authenticated decryption.
        Each vector is decrypted under the source binding and re-encrypted
        under the target binding as part of the same merge transaction.
        """

        key = self._key_provider.get_key()
        rows = self._connection.execute(
            "SELECT id, encrypted_vector FROM face_embeddings WHERE profile_id = ?",
            (str(source_id),),
        ).fetchall()
        for row in rows:
            plaintext = decrypt_bytes(
                key, row["encrypted_vector"], associated_data=str(source_id).encode()
            )
            re_encrypted = encrypt_bytes(key, plaintext, associated_data=str(target_id).encode())
            self._connection.execute(
                "UPDATE face_embeddings SET profile_id = ?, encrypted_vector = ? WHERE id = ?",
                (str(target_id), re_encrypted, row["id"]),
            )

    def add_embedding(
        self,
        profile_id: UUID,
        embedding: FaceEmbedding,
        *,
        quality_score: float,
        is_representative: bool = False,
        max_embeddings: int = 50,
    ) -> UUID:
        """Encrypt and persist one embedding vector for an existing profile.

        ``max_embeddings`` (M15's storage-limit deliverable) bounds
        unconstrained per-profile growth; the default matches
        ``EnrollmentConfig.maximum_samples_per_candidate`` so a normal
        promotion transferring a candidate's full sample set is never
        silently truncated. Callers should pass
        ``config.database.maximum_embeddings_per_profile`` explicitly.
        """

        self.get(profile_id)  # raises ProfileNotFoundError if missing
        current_count = self._connection.execute(
            "SELECT COUNT(*) FROM face_embeddings WHERE profile_id = ?", (str(profile_id),)
        ).fetchone()[0]
        if current_count >= max_embeddings:
            raise ProfileRepositoryError(
                f"profile {profile_id} already has the maximum {max_embeddings} embeddings"
            )
        key = self._key_provider.get_key()
        encrypted = encrypt_bytes(
            key, embedding.vector.tobytes(), associated_data=str(profile_id).encode()
        )
        embedding_id = uuid4()
        now = self._clock().isoformat()
        self._connection.execute(
            "INSERT INTO face_embeddings (id, profile_id, model_name, model_version, "
            "model_checksum, embedding_dimension, numeric_dtype, encrypted_vector, "
            "quality_score, source_image_path, is_representative, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                str(embedding_id),
                str(profile_id),
                embedding.model_name,
                embedding.model_version,
                embedding.model_checksum,
                embedding.dimension,
                embedding.numeric_dtype,
                encrypted,
                quality_score,
                int(is_representative),
                now,
            ),
        )
        return embedding_id

    def list_embeddings(self, profile_id: UUID) -> tuple[StoredEmbeddingMetadata, ...]:
        """Return embedding metadata for a profile, excluding vector bytes."""

        rows = self._connection.execute(
            "SELECT id, profile_id, model_name, model_version, model_checksum, "
            "embedding_dimension, numeric_dtype, quality_score, is_representative, "
            "created_at FROM face_embeddings WHERE profile_id = ? ORDER BY created_at ASC",
            (str(profile_id),),
        ).fetchall()
        return tuple(
            StoredEmbeddingMetadata(
                id=UUID(row["id"]),
                profile_id=UUID(row["profile_id"]),
                model_name=row["model_name"],
                model_version=row["model_version"],
                model_checksum=row["model_checksum"],
                embedding_dimension=row["embedding_dimension"],
                numeric_dtype=row["numeric_dtype"],
                quality_score=row["quality_score"],
                is_representative=bool(row["is_representative"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        )

    def get_embedding_vector(self, embedding_id: UUID) -> FaceEmbedding:
        """Decrypt and return one embedding's full vector on demand."""

        row = self._connection.execute(
            "SELECT * FROM face_embeddings WHERE id = ?", (str(embedding_id),)
        ).fetchone()
        if row is None:
            raise EmbeddingNotFoundError(str(embedding_id))
        key = self._key_provider.get_key()
        raw = decrypt_bytes(
            key, row["encrypted_vector"], associated_data=row["profile_id"].encode()
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

    def remove_embedding(self, embedding_id: UUID) -> None:
        """Permanently remove one embedding row."""

        cursor = self._connection.execute(
            "DELETE FROM face_embeddings WHERE id = ?", (str(embedding_id),)
        )
        if cursor.rowcount == 0:
            raise EmbeddingNotFoundError(str(embedding_id))

    def export_profile(self, profile_id: UUID) -> bytes:
        """Return an encrypted, self-contained export blob for one profile."""

        profile = self.get(profile_id)
        embeddings = []
        for metadata in self.list_embeddings(profile_id):
            embedding = self.get_embedding_vector(metadata.id)
            embeddings.append(
                {
                    "model_name": embedding.model_name,
                    "model_version": embedding.model_version,
                    "model_checksum": embedding.model_checksum,
                    "dimension": embedding.dimension,
                    "numeric_dtype": embedding.numeric_dtype,
                    "vector": embedding.vector.tolist(),
                    "quality_score": metadata.quality_score,
                    "is_representative": metadata.is_representative,
                }
            )
        payload = {
            "schema": "face-profile-export-v1",
            "display_name": profile.display_name,
            "priority": profile.priority,
            "is_owner": profile.is_owner,
            "metadata": dict(profile.metadata),
            "embeddings": embeddings,
        }
        plaintext = json.dumps(payload).encode("utf-8")
        key = self._key_provider.get_key()
        return encrypt_bytes(key, plaintext, associated_data=_EXPORT_ASSOCIATED_DATA)

    def import_profile(self, blob: bytes) -> Profile:
        """Decrypt an export blob and create a new profile with a fresh UUID.

        A new UUID is always allocated on import to avoid colliding with an
        existing profile identifier; callers that need to link imported and
        original identifiers must record that mapping themselves. Like
        ``merge``, this composes multiple writes into one atomic unit, so it
        manages its own commit/rollback.
        """

        key = self._key_provider.get_key()
        plaintext = decrypt_bytes(key, blob, associated_data=_EXPORT_ASSOCIATED_DATA)
        try:
            payload = json.loads(plaintext)
        except json.JSONDecodeError as error:
            raise ProfileRepositoryError("export payload is not valid JSON") from error
        if payload.get("schema") != "face-profile-export-v1":
            raise ProfileRepositoryError("unsupported export schema")
        try:
            profile = self.create(
                display_name=payload["display_name"],
                is_owner=bool(payload.get("is_owner", False)),
                priority=int(payload.get("priority", 0)),
                metadata=payload.get("metadata", {}),
            )
            for entry in payload.get("embeddings", []):
                vector = np.asarray(entry["vector"], dtype=np.float32)
                embedding = FaceEmbedding(
                    vector=vector,
                    model_name=entry["model_name"],
                    model_version=entry["model_version"],
                    model_checksum=entry["model_checksum"],
                    dimension=entry["dimension"],
                    numeric_dtype=entry["numeric_dtype"],
                )
                self.add_embedding(
                    profile.id,
                    embedding,
                    quality_score=entry["quality_score"],
                    is_representative=entry.get("is_representative", False),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return self.get(profile.id)


class ProfileSettingsRepository:
    """Durable storage for one profile's preferred device settings."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock | None = None) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._clock = clock or _default_clock

    def get(self, profile_id: UUID) -> DeviceSettings | None:
        """Return a profile's stored settings, or ``None`` if never saved."""

        row = self._connection.execute(
            "SELECT volume, brightness FROM profile_settings WHERE profile_id = ?",
            (str(profile_id),),
        ).fetchone()
        if row is None:
            return None
        return DeviceSettings(volume=row["volume"], brightness=row["brightness"])

    def upsert(self, profile_id: UUID, settings: DeviceSettings) -> None:
        """Create or replace a profile's stored settings."""

        now = self._clock().isoformat()
        self._connection.execute(
            "INSERT INTO profile_settings (profile_id, volume, brightness, "
            "additional_settings_json, updated_at) VALUES (?, ?, ?, '{}', ?) "
            "ON CONFLICT(profile_id) DO UPDATE SET volume = excluded.volume, "
            "brightness = excluded.brightness, updated_at = excluded.updated_at",
            (str(profile_id), settings.volume, settings.brightness, now),
        )


class RecognitionEventRepository:
    """Append-only durable storage for recognition-domain events."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock | None = None) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._clock = clock or _default_clock

    def record(
        self,
        *,
        event_type: str,
        profile_id: UUID | None = None,
        candidate_id: UUID | None = None,
        track_id: int | None = None,
        similarity: float | None = None,
        camera_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> UUID:
        """Append one durable event and return its identifier."""

        next_sequence = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM recognition_events"
        ).fetchone()[0]
        event_id = uuid4()
        now = self._clock().isoformat()
        self._connection.execute(
            "INSERT INTO recognition_events (id, event_type, profile_id, candidate_id, "
            "track_id, similarity, camera_id, occurred_at, sequence, correlation_id, "
            "metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(event_id),
                event_type,
                str(profile_id) if profile_id is not None else None,
                str(candidate_id) if candidate_id is not None else None,
                track_id,
                similarity,
                camera_id,
                now,
                next_sequence,
                correlation_id,
                json.dumps(dict(metadata or {})),
            ),
        )
        return event_id

    def list_recent(self, *, limit: int = 100) -> tuple[RecognitionEventRecord, ...]:
        """Return the most recent durable events, newest first."""

        if limit < 1 or limit > 1000:
            raise ProfileRepositoryError("limit must be between 1 and 1000")
        rows = self._connection.execute(
            "SELECT * FROM recognition_events ORDER BY sequence DESC LIMIT ?", (limit,)
        ).fetchall()
        return tuple(
            RecognitionEventRecord(
                id=UUID(row["id"]),
                event_type=row["event_type"],
                profile_id=UUID(row["profile_id"]) if row["profile_id"] else None,
                candidate_id=UUID(row["candidate_id"]) if row["candidate_id"] else None,
                track_id=row["track_id"],
                similarity=row["similarity"],
                camera_id=row["camera_id"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                sequence=row["sequence"],
                correlation_id=row["correlation_id"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        )


@dataclass(slots=True)
class ProfileDatabase:
    """Bundled connection and repositories for one open profile database."""

    connection: sqlite3.Connection
    profiles: ProfileRepository
    settings: ProfileSettingsRepository
    events: RecognitionEventRepository

    def commit(self) -> None:
        """Commit the current transaction, covering every write since the last commit."""

        self.connection.commit()

    def rollback(self) -> None:
        """Discard uncommitted writes since the last commit."""

        self.connection.rollback()

    def close(self) -> None:
        """Close the underlying connection."""

        self.connection.close()
