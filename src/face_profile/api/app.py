"""FastAPI application factory for the M12 headless daemon.

Nothing in this module runs a server; the CLI's ``serve`` command does
that via uvicorn. Non-loopback binding without a configured auth token is
already rejected by ``config.APIConfig``'s validator before
:func:`create_app` is ever reached.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from face_profile.api.rate_limit import RateLimiter
from face_profile.api.routes import router
from face_profile.api.worker import PipelineWorker
from face_profile.camera.factory import create_frame_source
from face_profile.config import AppConfig
from face_profile.database.candidate_repository import CandidateRepository
from face_profile.database.factory import create_profile_database
from face_profile.database.keys import LocalFileKeyProvider
from face_profile.database.repository import ProfileDatabase
from face_profile.enrollment.factory import create_candidate_manager, create_candidate_promoter
from face_profile.liveness.passive import create_passive_liveness_evaluator
from face_profile.presence.active_user import ActiveUserSelector
from face_profile.presence.factory import create_active_user_selector
from face_profile.recognition.factory import create_recognizer
from face_profile.settings import DeviceSettings
from face_profile.settings.factory import (
    create_last_used_preference_service,
    create_settings_adapter,
)
from face_profile.settings.last_used_service import LastUsedPreferenceService
from face_profile.vision.alignment import create_aligner
from face_profile.vision.embedding import create_embedding_generator
from face_profile.vision.factory import create_face_detector
from face_profile.vision.quality import create_quality_evaluator
from face_profile.vision.tracking import create_tracker

_DASHBOARD_HTML_PATH = Path(__file__).resolve().parent.parent / "ui" / "dashboard.html"


def create_app(config: AppConfig) -> FastAPI:
    """Build the fully wired FastAPI application for the given configuration."""

    database = create_profile_database(config.database)
    candidates = _build_candidate_repository(config, database)
    promoter = create_candidate_promoter(config, database) if database is not None else None
    settings_adapter = create_settings_adapter(
        config.settings,
        initial=DeviceSettings(
            volume=config.settings.volume, brightness=config.settings.brightness
        ),
    )
    active_user_selector = create_active_user_selector(config.active_user)
    preference_service = (
        create_last_used_preference_service(
            config.preference_learning, adapter=settings_adapter, database=database
        )
        if database is not None
        else None
    )
    worker = _build_worker(
        config,
        database=database,
        active_user_selector=active_user_selector,
        preference_service=preference_service,
    )
    rate_limiter = RateLimiter(limit_per_minute=config.api.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if worker is not None:
            worker.start()
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            if database is not None:
                database.close()

    app = FastAPI(title="Face Profile API", version="1", lifespan=lifespan)
    app.state.config = config
    app.state.database = database
    app.state.candidates = candidates
    app.state.promoter = promoter
    app.state.settings_adapter = settings_adapter
    app.state.worker = worker

    @app.middleware("http")
    async def _enforce_limits(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > config.api.max_request_body_bytes:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"error_code": "request_too_large"},
            )
        client_key = request.client.host if request.client else "unknown"
        if request.url.path != "/api/v1/health" and not rate_limiter.allow(client_key):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"error_code": "rate_limited"},
            )
        return await call_next(request)

    app.include_router(router)

    if config.ui.enabled:
        dashboard_html = _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def dashboard() -> str:
            return dashboard_html

    return app


def _build_candidate_repository(
    config: AppConfig, database: ProfileDatabase | None
) -> CandidateRepository | None:
    if database is None or not config.enrollment.enabled:
        return None
    key_provider = LocalFileKeyProvider(config.database.key_path)
    return CandidateRepository(database.connection, key_provider=key_provider)


def _build_worker(
    config: AppConfig,
    *,
    database: ProfileDatabase | None,
    active_user_selector: ActiveUserSelector | None,
    preference_service: LastUsedPreferenceService | None,
) -> PipelineWorker | None:
    if not config.camera.enabled:
        return None
    frame_source = create_frame_source(config.camera)
    detector = create_face_detector(config.detection)
    tracker = create_tracker(config.tracking)
    if frame_source is None or detector is None or tracker is None:
        return None
    recognizer = create_recognizer(config, database.profiles) if database is not None else None
    candidate_manager = create_candidate_manager(config, database) if database is not None else None
    return PipelineWorker(
        frame_source=frame_source,
        detector=detector,
        tracker=tracker,
        quality_evaluator=create_quality_evaluator(config.quality),
        aligner=create_aligner(config.quality),
        embedder=create_embedding_generator(config.embedding),
        recognizer=recognizer,
        candidate_manager=candidate_manager,
        active_user_selector=active_user_selector,
        preference_service=preference_service,
        passive_liveness_evaluator=create_passive_liveness_evaluator(config.liveness),
        profiles=database.profiles if database is not None else None,
        poll_interval_seconds=config.api.worker_poll_interval_seconds,
    )
