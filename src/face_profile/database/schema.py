"""Versioned SQLite schema migrations for M6 persistence.

Migrations use plain SQL over the standard-library ``sqlite3`` module
rather than an ORM/Alembic stack, per CODING_STANDARDS/CONTRIBUTING
guidance to prefer an existing dependency-light solution when one is
sufficient. Every migration is recorded in ``schema_version`` so restarts
apply only pending migrations.

Migration v1 (M6) creates the profile, embedding, settings, and event
schema. Migration v2 (M8) adds candidate lifecycle and candidate-embedding
storage. ``recognition_events.candidate_id`` remains a plain column without
a foreign key: SQLite cannot add a ``REFERENCES`` constraint to an existing
column without rebuilding the table, so retrofitting that constraint is a
documented, deferred follow-up rather than a risky migration performed here.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

_MIGRATION_V1 = """
CREATE TABLE profiles (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    is_owner INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    merged_into_profile_id TEXT REFERENCES profiles(id),
    enrollment_review_status TEXT NOT NULL DEFAULT 'not_applicable',
    retention_expires_at TEXT,
    optimistic_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_profiles_status ON profiles(status);
CREATE INDEX idx_profiles_retention_expires_at ON profiles(retention_expires_at);

CREATE TABLE face_embeddings (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_checksum TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    numeric_dtype TEXT NOT NULL,
    encrypted_vector BLOB NOT NULL,
    quality_score REAL NOT NULL,
    source_image_path TEXT,
    is_representative INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_face_embeddings_profile_id ON face_embeddings(profile_id);

CREATE TABLE profile_settings (
    profile_id TEXT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    volume INTEGER NOT NULL,
    brightness INTEGER NOT NULL,
    additional_settings_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE recognition_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    profile_id TEXT REFERENCES profiles(id),
    candidate_id TEXT,
    track_id INTEGER,
    similarity REAL,
    camera_id TEXT,
    occurred_at TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    correlation_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_recognition_events_profile_id ON recognition_events(profile_id);
CREATE INDEX idx_recognition_events_occurred_at ON recognition_events(occurred_at);
"""

_MIGRATION_V2 = """
CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    temporary_name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    aggregate_quality REAL NOT NULL DEFAULT 0.0,
    review_status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TEXT,
    retention_expires_at TEXT,
    promoted_profile_id TEXT REFERENCES profiles(id),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_candidates_status ON candidates(status);
CREATE INDEX idx_candidates_retention_expires_at ON candidates(retention_expires_at);

CREATE TABLE candidate_embeddings (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_checksum TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    numeric_dtype TEXT NOT NULL,
    encrypted_vector BLOB NOT NULL,
    quality_score REAL NOT NULL,
    source_camera_id TEXT,
    source_track_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_candidate_embeddings_candidate_id ON candidate_embeddings(candidate_id);

-- A single-row counter backing predictable "Unknown-000001"-style temporary
-- names. Not safe under concurrent writers (documented limitation, same as
-- recognition_events.sequence; both predate M12's concurrent workers).
CREATE TABLE candidate_name_sequence (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_value INTEGER NOT NULL
);

INSERT INTO candidate_name_sequence (id, next_value) VALUES (1, 1);
"""

# Each entry is (version, description, forward SQL, downgrade note). There is
# no downgrade script for v1 because it is the initial schema; later
# migrations must record an explicit downgrade strategy or data-loss
# assessment per CONTRIBUTING.md's database-change requirements.
MIGRATIONS: tuple[tuple[int, str, str, str], ...] = (
    (
        1,
        "Initial profile, embedding, settings, and event schema",
        _MIGRATION_V1,
        "No downgrade: this is the initial schema.",
    ),
    (
        2,
        "Candidate lifecycle, candidate embeddings, and temporary-name sequence",
        _MIGRATION_V2,
        "Downgrade would drop the candidates, candidate_embeddings, and "
        "candidate_name_sequence tables, permanently losing all in-flight "
        "candidate biometric data and review history; no automated "
        "downgrade script is provided.",
    ),
)


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Apply pending migrations in order and return the resulting version."""

    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    connection.commit()
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    current = int(row[0])
    for version, description, sql, _downgrade in MIGRATIONS:
        if version <= current:
            continue
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_version (version, description, applied_at) VALUES (?, ?, ?)",
            (version, description, datetime.now(UTC).isoformat()),
        )
        connection.commit()
        current = version
    return current
