"""M12 versioned REST/WebSocket routes.

Routes call application services and repositories directly (there is no
separate application-service layer yet); they perform no domain decisions
themselves, translating domain exceptions to machine-readable errors at
this boundary, per CODING_STANDARDS.md §9/§17. Every state-changing route
depends on :func:`face_profile.api.auth.require_auth`.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import Response

from face_profile.api.auth import check_websocket_auth, require_auth
from face_profile.api.schemas import (
    CandidateApproveRequest,
    CandidateListResponse,
    CandidateRejectRequest,
    CandidateResponse,
    CapabilitiesResponse,
    DeviceSettingsResponse,
    EventListResponse,
    EventResponse,
    FaceListResponse,
    FaceResponse,
    HealthResponse,
    MetricsResponse,
    ProfileCreateRequest,
    ProfileExportRequest,
    ProfileExportResponse,
    ProfileImportRequest,
    ProfileListResponse,
    ProfileMergeRequest,
    ProfilePatchRequest,
    ProfileResponse,
    ProfileSettingsRequest,
    StatusResponse,
)
from face_profile.api.worker import WorkerState
from face_profile.database.candidate_repository import (
    CandidateNotFoundError,
    CandidateRepository,
    CandidateStateError,
)
from face_profile.database.models import CandidateStatus, ProfileStatus
from face_profile.database.repository import (
    ConcurrencyConflictError,
    ProfileDatabase,
    ProfileNotFoundError,
    ProfileRepositoryError,
)
from face_profile.enrollment.promotion import (
    CandidateNotReadyError,
    DuplicateProfileError,
    PromotionError,
)
from face_profile.settings import DeviceSettings

router = APIRouter(prefix="/api/v1")

AuthDep = Annotated[None, Depends(require_auth)]


def _domain_error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"error_code": error_code, "message": message}
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/status", response_model=StatusResponse, dependencies=[Depends(require_auth)])
async def get_status(request: Request) -> StatusResponse:
    state = request.app.state
    worker = getattr(state, "worker", None)
    worker_health = worker.health() if worker is not None else None
    return StatusResponse(
        service_state="running",
        worker_state=worker_health.state.value if worker_health else WorkerState.STOPPED.value,
        frames_processed=worker_health.frames_processed if worker_health else 0,
        frames_detected=worker_health.frames_detected if worker_health else 0,
        worker_last_error=worker_health.last_error if worker_health else None,
        database_enabled=state.database is not None,
        camera_enabled=state.config.camera.enabled,
    )


@router.get("/metrics", response_model=MetricsResponse, dependencies=[Depends(require_auth)])
async def get_metrics(request: Request) -> MetricsResponse:
    """M15 metrics export: worker throughput plus aggregate counts only."""

    state = request.app.state
    worker = getattr(state, "worker", None)
    worker_health = worker.health() if worker is not None else None
    frames_processed = worker_health.frames_processed if worker_health else 0
    frames_detected = worker_health.frames_detected if worker_health else 0
    skip_ratio = 1.0 - (frames_detected / frames_processed) if frames_processed > 0 else 0.0
    active_profiles = 0
    collecting = 0
    ready = 0
    database: ProfileDatabase | None = state.database
    if database is not None:
        active_profiles = len(database.profiles.list(status=ProfileStatus.ACTIVE, limit=1000))
        candidates = getattr(state, "candidates", None)
        if candidates is not None:
            collecting = len(candidates.list(status=CandidateStatus.COLLECTING, limit=1000))
            ready = len(candidates.list(status=CandidateStatus.READY_FOR_REVIEW, limit=1000))
    return MetricsResponse(
        worker_state=worker_health.state.value if worker_health else WorkerState.STOPPED.value,
        frames_processed=frames_processed,
        frames_detected=frames_detected,
        detection_skip_ratio=skip_ratio,
        active_profile_count=active_profiles,
        collecting_candidate_count=collecting,
        ready_for_review_candidate_count=ready,
    )


_PREVIEW_CACHE_CONTROL = "no-store, no-cache, must-revalidate"


@router.get("/preview/latest.jpg", dependencies=[Depends(require_auth)])
async def get_latest_preview(request: Request) -> Response:
    """Return the pipeline worker's latest annotated preview JPEG, if any.

    Authenticated the same way as every other route (bearer header via
    ``require_auth``; never a URL/query token, so it can never leak into
    logs, browser history, or a Referer header). Exact status mapping,
    chosen so a dashboard can distinguish "will never work here" from
    "transiently not ready yet":

    - 404 ``preview_disabled``: ``ui.webcam_preview_enabled`` is False for
      this deployment's configuration -- the feature itself is off.
    - 409 ``worker_unavailable``: the feature is enabled but there is no
      running pipeline worker to produce frames (camera disabled, or the
      worker has stopped/failed).
    - 503 ``preview_not_ready``: the worker is running but has not yet
      encoded a first frame (e.g. immediately after startup).
    """

    config = request.app.state.config
    if not config.ui.webcam_preview_enabled:
        raise _domain_error(
            status.HTTP_404_NOT_FOUND, "preview_disabled", "the webcam preview is disabled"
        )
    worker = getattr(request.app.state, "worker", None)
    if worker is None or worker.health().state not in (WorkerState.RUNNING, WorkerState.PAUSED):
        raise _domain_error(
            status.HTTP_409_CONFLICT,
            "worker_unavailable",
            "no running pipeline worker is available to produce a preview",
        )
    jpeg = worker.latest_preview_jpeg()
    if jpeg is None:
        raise _domain_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "preview_not_ready",
            "no preview frame has been generated yet",
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": _PREVIEW_CACHE_CONTROL},
    )


def _require_database(request: Request) -> ProfileDatabase:
    database: ProfileDatabase | None = request.app.state.database
    if database is None:
        raise _domain_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_disabled",
            "the profile database is disabled",
        )
    return database


def _require_candidates(request: Request) -> CandidateRepository:
    """Return the candidate repository, or a controlled 503 instead of a ``None`` deref.

    ``app.state.candidates`` is ``None`` whenever the database or enrollment
    is disabled (see ``api/app.py``'s ``_build_candidate_repository``); the
    candidate routes must fail closed with a machine-readable error rather
    than raise ``AttributeError`` on ``None.list(...)``.
    """

    candidates: CandidateRepository | None = getattr(request.app.state, "candidates", None)
    if candidates is None:
        raise _domain_error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "enrollment_disabled", "enrollment is disabled"
        )
    return candidates


def _parse_profile_status(value: str | None) -> ProfileStatus | None:
    if value is None:
        return None
    try:
        return ProfileStatus(value)
    except ValueError as error:
        raise _domain_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_status_filter",
            f"unknown profile status: {value}",
        ) from error


def _parse_candidate_status(value: str | None) -> CandidateStatus | None:
    if value is None:
        return None
    try:
        return CandidateStatus(value)
    except ValueError as error:
        raise _domain_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_status_filter",
            f"unknown candidate status: {value}",
        ) from error


@router.get("/profiles", response_model=ProfileListResponse, dependencies=[Depends(require_auth)])
async def list_profiles(
    request: Request, status_filter: str | None = None, limit: int = 50, offset: int = 0
) -> ProfileListResponse:
    database = _require_database(request)
    parsed_status = _parse_profile_status(status_filter)
    try:
        profiles = database.profiles.list(status=parsed_status, limit=limit, offset=offset)
    except ProfileRepositoryError as error:
        raise _domain_error(status.HTTP_400_BAD_REQUEST, "invalid_request", str(error)) from error
    return ProfileListResponse(
        items=[ProfileResponse.from_domain(p) for p in profiles], limit=limit, offset=offset
    )


@router.post(
    "/profiles",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
async def create_profile(request: Request, body: ProfileCreateRequest) -> ProfileResponse:
    database = _require_database(request)
    try:
        profile = database.profiles.create(
            display_name=body.display_name, is_owner=body.is_owner, priority=body.priority
        )
        database.events.record(event_type="ProfileCreated", profile_id=profile.id)
        database.commit()
    except ProfileRepositoryError as error:
        raise _domain_error(status.HTTP_400_BAD_REQUEST, "invalid_request", str(error)) from error
    return ProfileResponse.from_domain(profile)


@router.get(
    "/profiles/{profile_id}", response_model=ProfileResponse, dependencies=[Depends(require_auth)]
)
async def get_profile(request: Request, profile_id: UUID) -> ProfileResponse:
    database = _require_database(request)
    try:
        profile = database.profiles.get(profile_id)
    except ProfileNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "profile_not_found", str(error)) from error
    return ProfileResponse.from_domain(profile)


@router.patch(
    "/profiles/{profile_id}", response_model=ProfileResponse, dependencies=[Depends(require_auth)]
)
async def patch_profile(
    request: Request, profile_id: UUID, body: ProfilePatchRequest
) -> ProfileResponse:
    database = _require_database(request)
    status_value = _parse_profile_status(body.status)
    try:
        profile = database.profiles.update(
            profile_id,
            expected_version=body.expected_version,
            display_name=body.display_name,
            priority=body.priority,
            status=status_value,
            metadata=body.metadata,
        )
        database.events.record(event_type="ProfileUpdated", profile_id=profile.id)
        database.commit()
    except ProfileNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "profile_not_found", str(error)) from error
    except ConcurrencyConflictError as error:
        database.rollback()
        raise _domain_error(status.HTTP_409_CONFLICT, "concurrency_conflict", str(error)) from error
    return ProfileResponse.from_domain(profile)


@router.delete(
    "/profiles/{profile_id}", response_model=ProfileResponse, dependencies=[Depends(require_auth)]
)
async def delete_profile(
    request: Request, profile_id: UUID, expected_version: int
) -> ProfileResponse:
    database = _require_database(request)
    try:
        profile = database.profiles.soft_delete(
            profile_id,
            expected_version=expected_version,
            retention_days=request.app.state.config.database.retention_days,
        )
        database.events.record(event_type="ProfileDeleted", profile_id=profile_id)
        database.commit()
    except ProfileNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "profile_not_found", str(error)) from error
    except ConcurrencyConflictError as error:
        database.rollback()
        raise _domain_error(status.HTTP_409_CONFLICT, "concurrency_conflict", str(error)) from error
    return ProfileResponse.from_domain(profile)


@router.post(
    "/profiles/{profile_id}/merge",
    response_model=ProfileResponse,
    dependencies=[Depends(require_auth)],
)
async def merge_profile(
    request: Request, profile_id: UUID, body: ProfileMergeRequest
) -> ProfileResponse:
    database = _require_database(request)
    try:
        profile = database.profiles.merge(
            source_id=profile_id,
            target_id=body.target_id,
            expected_source_version=body.source_expected_version,
            expected_target_version=body.target_expected_version,
        )
        database.events.record(event_type="ProfileMerged", profile_id=profile.id)
        database.commit()
    except ProfileNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "profile_not_found", str(error)) from error
    except ConcurrencyConflictError as error:
        raise _domain_error(status.HTTP_409_CONFLICT, "concurrency_conflict", str(error)) from error
    except ProfileRepositoryError as error:
        raise _domain_error(status.HTTP_400_BAD_REQUEST, "invalid_request", str(error)) from error
    return ProfileResponse.from_domain(profile)


@router.post(
    "/profiles/export", response_model=ProfileExportResponse, dependencies=[Depends(require_auth)]
)
async def export_profile(request: Request, body: ProfileExportRequest) -> ProfileExportResponse:
    database = _require_database(request)
    try:
        blob = database.profiles.export_profile(body.profile_id)
    except ProfileNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "profile_not_found", str(error)) from error
    return ProfileExportResponse(blob_base64=base64.b64encode(blob).decode("ascii"))


@router.post(
    "/profiles/import", response_model=ProfileResponse, dependencies=[Depends(require_auth)]
)
async def import_profile(request: Request, body: ProfileImportRequest) -> ProfileResponse:
    database = _require_database(request)
    try:
        blob = base64.b64decode(body.blob_base64, validate=True)
    except binascii.Error as error:
        raise _domain_error(
            status.HTTP_400_BAD_REQUEST, "invalid_request", "invalid base64"
        ) from error
    try:
        profile = database.profiles.import_profile(blob)
        database.events.record(event_type="ProfileCreated", profile_id=profile.id)
        database.commit()
    except ProfileRepositoryError as error:
        raise _domain_error(status.HTTP_400_BAD_REQUEST, "invalid_request", str(error)) from error
    return ProfileResponse.from_domain(profile)


@router.get(
    "/candidates", response_model=CandidateListResponse, dependencies=[Depends(require_auth)]
)
async def list_candidates(
    request: Request, status_filter: str | None = None, limit: int = 50, offset: int = 0
) -> CandidateListResponse:
    candidates = _require_candidates(request)
    parsed_status = _parse_candidate_status(status_filter)
    result = candidates.list(status=parsed_status, limit=limit, offset=offset)
    return CandidateListResponse(
        items=[CandidateResponse.from_domain(c) for c in result], limit=limit, offset=offset
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=CandidateResponse,
    dependencies=[Depends(require_auth)],
)
async def get_candidate(request: Request, candidate_id: UUID) -> CandidateResponse:
    candidates = _require_candidates(request)
    try:
        candidate = candidates.get(candidate_id)
    except CandidateNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "candidate_not_found", str(error)) from error
    return CandidateResponse.from_domain(candidate)


@router.post(
    "/candidates/{candidate_id}/approve",
    response_model=ProfileResponse,
    dependencies=[Depends(require_auth)],
)
async def approve_candidate(
    request: Request, candidate_id: UUID, body: CandidateApproveRequest
) -> ProfileResponse:
    _require_database(request)
    promoter = request.app.state.promoter
    if promoter is None:
        raise _domain_error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "enrollment_disabled", "enrollment is disabled"
        )
    try:
        profile = promoter.promote(candidate_id, reviewed_by=body.reviewed_by)
    except CandidateNotReadyError as error:
        raise _domain_error(status.HTTP_409_CONFLICT, "not_ready", str(error)) from error
    except DuplicateProfileError as error:
        raise _domain_error(status.HTTP_409_CONFLICT, "duplicate_profile", str(error)) from error
    except PromotionError as error:
        raise _domain_error(status.HTTP_400_BAD_REQUEST, "promotion_failed", str(error)) from error
    return ProfileResponse.from_domain(profile)


@router.post(
    "/candidates/{candidate_id}/reject",
    response_model=CandidateResponse,
    dependencies=[Depends(require_auth)],
)
async def reject_candidate(
    request: Request, candidate_id: UUID, body: CandidateRejectRequest
) -> CandidateResponse:
    database = _require_database(request)
    candidates = _require_candidates(request)
    try:
        candidate = candidates.reject(
            candidate_id, reason=body.reason, reviewed_by=body.reviewed_by
        )
        database.commit()
    except CandidateNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "candidate_not_found", str(error)) from error
    except CandidateStateError as error:
        database.rollback()
        raise _domain_error(status.HTTP_409_CONFLICT, "invalid_state", str(error)) from error
    return CandidateResponse.from_domain(candidate)


@router.delete("/candidates/{candidate_id}", dependencies=[Depends(require_auth)])
async def delete_candidate(request: Request, candidate_id: UUID) -> dict[str, str]:
    database = _require_database(request)
    candidates = _require_candidates(request)
    try:
        candidates.delete(candidate_id)
        database.commit()
    except CandidateNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "candidate_not_found", str(error)) from error
    return {"status": "deleted"}


@router.get("/events", response_model=EventListResponse, dependencies=[Depends(require_auth)])
async def list_events(request: Request, limit: int = 50) -> EventListResponse:
    database = _require_database(request)
    events = database.events.list_recent(limit=limit)
    return EventListResponse(
        items=[
            EventResponse(
                id=e.id,
                event_type=e.event_type,
                profile_id=e.profile_id,
                candidate_id=e.candidate_id,
                track_id=e.track_id,
                similarity=e.similarity,
                camera_id=e.camera_id,
                occurred_at=e.occurred_at.isoformat(),
                sequence=e.sequence,
                correlation_id=e.correlation_id,
            )
            for e in events
        ],
        limit=limit,
    )


@router.get(
    "/settings/current", response_model=DeviceSettingsResponse, dependencies=[Depends(require_auth)]
)
async def get_current_settings(request: Request) -> DeviceSettingsResponse:
    adapter = request.app.state.settings_adapter
    current = adapter.read_current()
    return DeviceSettingsResponse(volume=current.volume, brightness=current.brightness)


@router.get(
    "/profiles/{profile_id}/settings",
    response_model=DeviceSettingsResponse,
    dependencies=[Depends(require_auth)],
)
async def get_profile_settings(request: Request, profile_id: UUID) -> DeviceSettingsResponse:
    database = _require_database(request)
    try:
        database.profiles.get(profile_id)
    except ProfileNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "profile_not_found", str(error)) from error
    saved = database.settings.get(profile_id)
    if saved is None:
        raise _domain_error(
            status.HTTP_404_NOT_FOUND,
            "settings_not_found",
            "no settings have been saved for this profile",
        )
    return DeviceSettingsResponse(volume=saved.volume, brightness=saved.brightness)


@router.put(
    "/profiles/{profile_id}/settings",
    response_model=DeviceSettingsResponse,
    dependencies=[Depends(require_auth)],
)
async def put_profile_settings(
    request: Request, profile_id: UUID, body: ProfileSettingsRequest
) -> DeviceSettingsResponse:
    database = _require_database(request)
    try:
        database.profiles.get(profile_id)
    except ProfileNotFoundError as error:
        raise _domain_error(status.HTTP_404_NOT_FOUND, "profile_not_found", str(error)) from error
    settings = DeviceSettings(volume=body.volume, brightness=body.brightness)
    database.settings.upsert(profile_id, settings)
    database.events.record(
        event_type="SettingsChanged",
        profile_id=profile_id,
        metadata={"volume": settings.volume, "brightness": settings.brightness},
    )
    database.commit()
    return DeviceSettingsResponse(volume=settings.volume, brightness=settings.brightness)


@router.post("/system/pause", dependencies=[Depends(require_auth)])
async def pause_system(request: Request) -> dict[str, str]:
    worker = request.app.state.worker
    if worker is not None:
        worker.pause()
    return {"status": "paused"}


@router.post("/system/resume", dependencies=[Depends(require_auth)])
async def resume_system(request: Request) -> dict[str, str]:
    worker = request.app.state.worker
    if worker is not None:
        worker.resume()
    return {"status": "resumed"}


@router.get("/faces", response_model=FaceListResponse, dependencies=[Depends(require_auth)])
async def list_faces(request: Request) -> FaceListResponse:
    """Return bounded, privacy-safe metadata for every currently-tracked face.

    Never images, crops, or embeddings -- see ``api/worker.py``'s
    ``FaceSnapshot``. A disabled or not-yet-started worker is reported as an
    empty list rather than an error, matching ``/status``'s treatment of a
    missing worker.
    """

    worker = getattr(request.app.state, "worker", None)
    if worker is None:
        return FaceListResponse(items=[])
    return FaceListResponse(
        items=[FaceResponse.from_domain(face) for face in worker.latest_faces()]
    )


@router.get(
    "/system/capabilities",
    response_model=CapabilitiesResponse,
    dependencies=[Depends(require_auth)],
)
async def get_capabilities(request: Request) -> CapabilitiesResponse:
    """Return a safe capability/privacy summary: booleans and statuses only.

    Distinct from ``/status`` (worker throughput/state) and ``/settings/current``
    (live device values) -- this answers "what is this deployment allowed and
    configured to do" for a UI to render controls conditionally.
    """

    state = request.app.state
    config = state.config
    worker = getattr(state, "worker", None)
    worker_health = worker.health() if worker is not None else None
    return CapabilitiesResponse(
        api_auth_required=config.api.auth_token is not None,
        ui_enabled=config.ui.enabled,
        webcam_preview_enabled=config.ui.webcam_preview_enabled,
        camera_enabled=config.camera.enabled,
        database_enabled=state.database is not None,
        enrollment_enabled=config.enrollment.enabled,
        settings_backend=config.settings.backend,
        worker_state=worker_health.state.value if worker_health else WorkerState.STOPPED.value,
    )


@router.websocket("/events/live")
async def events_live(websocket: WebSocket, token: str | None = None) -> None:
    configured_token = websocket.app.state.config.api.auth_token
    if not check_websocket_auth(token, configured_token=configured_token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    database = websocket.app.state.database
    if database is None:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return
    last_sequence = 0
    try:
        while True:
            events = database.events.list_recent(limit=20)
            new_events = [e for e in events if e.sequence > last_sequence]
            for event in sorted(new_events, key=lambda e: e.sequence):
                await websocket.send_json(
                    {
                        "id": str(event.id),
                        "event_type": event.event_type,
                        "profile_id": str(event.profile_id) if event.profile_id else None,
                        "candidate_id": str(event.candidate_id) if event.candidate_id else None,
                        "sequence": event.sequence,
                        "occurred_at": event.occurred_at.isoformat(),
                    }
                )
                last_sequence = max(last_sequence, event.sequence)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
