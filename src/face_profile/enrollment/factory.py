"""Construct M8 enrollment components from validated configuration."""

from __future__ import annotations

from face_profile.config import AppConfig
from face_profile.database.candidate_repository import CandidateRepository
from face_profile.database.keys import LocalFileKeyProvider
from face_profile.database.repository import ProfileDatabase
from face_profile.enrollment.manager import CandidateManager
from face_profile.enrollment.promotion import CandidatePromoter


def create_candidate_manager(
    config: AppConfig,
    database: ProfileDatabase,
) -> CandidateManager | None:
    """Build the configured candidate manager, or None unless explicitly enabled."""

    if not config.enrollment.enabled:
        return None
    candidates = _candidate_repository(config, database)
    automatic_promoter = (
        _candidate_promoter(config, database, candidates)
        if config.enrollment.automatic_promotion
        else None
    )
    return CandidateManager(
        candidates=candidates,
        profiles=database.profiles,
        config=config.enrollment,
        automatic_promoter=automatic_promoter,
    )


def create_candidate_promoter(
    config: AppConfig,
    database: ProfileDatabase,
) -> CandidatePromoter | None:
    """Build the configured candidate promoter, or None unless explicitly enabled."""

    if not config.enrollment.enabled:
        return None
    candidates = _candidate_repository(config, database)
    return _candidate_promoter(config, database, candidates)


def _candidate_promoter(
    config: AppConfig,
    database: ProfileDatabase,
    candidates: CandidateRepository,
) -> CandidatePromoter:
    return CandidatePromoter(
        connection=database.connection,
        candidates=candidates,
        profiles=database.profiles,
        events=database.events,
        duplicate_profile_threshold=config.enrollment.duplicate_profile_threshold,
        require_liveness=config.liveness.enabled,
        max_embeddings_per_profile=config.database.maximum_embeddings_per_profile,
    )


def _candidate_repository(config: AppConfig, database: ProfileDatabase) -> CandidateRepository:
    key_provider = LocalFileKeyProvider(config.database.key_path)
    return CandidateRepository(database.connection, key_provider=key_provider)
