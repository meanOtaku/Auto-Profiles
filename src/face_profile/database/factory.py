"""Construct the M6 profile database from validated configuration."""

from __future__ import annotations

from face_profile.config import DatabaseConfig
from face_profile.database.connection import open_database
from face_profile.database.keys import LocalFileKeyProvider
from face_profile.database.repository import (
    ProfileDatabase,
    ProfileRepository,
    ProfileSettingsRepository,
    RecognitionEventRepository,
)


def create_profile_database(config: DatabaseConfig) -> ProfileDatabase | None:
    """Open the configured, migrated, encryption-ready database, or return None.

    Returns ``None`` unless ``database.enabled`` is explicitly true, matching
    the disabled-by-default factory pattern used since M1, and satisfying
    the rule that permanent biometric persistence requires an explicit
    opt-in with encryption already wired.
    """

    if not config.enabled:
        return None
    connection = open_database(config.path)
    key_provider = LocalFileKeyProvider(config.key_path)
    return ProfileDatabase(
        connection=connection,
        profiles=ProfileRepository(connection, key_provider=key_provider),
        settings=ProfileSettingsRepository(connection),
        events=RecognitionEventRepository(connection),
    )
