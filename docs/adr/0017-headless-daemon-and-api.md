# ADR 0017: Headless Daemon, REST/WebSocket API, and Fail-Closed Security

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M12

## Context

M12 must run the complete system without a graphical window: profiles persisting across restarts, working health reporting, no effect from UI disconnection, a versioned `/api/v1` surface that is authenticated, authorized, rate-limited, and audited for administrative operations, and non-loopback deployment that fails closed without production security configuration.

## Decision

**Stack.** FastAPI + uvicorn + `websockets`, per HERMES's recommended stack; `httpx` (dev-only, for `TestClient`-based verification without opening a real port). All added via `uv add`/`uv add --dev` with pinned versions, consistent with prior milestones' dependency additions.

**Fail-closed configuration.** `config.APIConfig` rejects, at configuration-*load* time (not merely at request time), an enabled API bound beyond loopback (`127.0.0.1`/`::1`/`localhost`) without an explicit `auth_token` — the same pattern M8 used for `automatic_promotion`. Verified in this session: a non-loopback, tokenless config is rejected before `create_app()` is ever reached (`ConfigurationError`, exit code 2), and a disabled API is refused separately by the CLI (exit code 3).

**Auth and rate limiting.** `api/auth.py`'s `require_auth` dependency enforces a bearer token via constant-time comparison whenever one is configured — including on loopback, if the operator opted in — and is attached to every route except `/health`. `api/rate_limit.py`'s `RateLimiter` is a simple in-memory fixed-window limiter keyed by client host, applied as HTTP middleware (health-exempt); a shared-store limiter for multi-instance deployments is a documented gap. A request-size middleware check rejects oversized bodies before parsing.

**Routes (`api/routes.py`).** Every route from HERMES's required surface is implemented against the real M6–M11 repositories/services: profiles (list/get/patch/delete/merge/export/import), candidates (list/get/approve/reject/delete), events, current/per-profile settings, and system pause/resume, plus `WS /api/v1/events/live`. Routes translate domain exceptions to machine-readable `{error_code, message}` bodies at this boundary and contain no domain decisions themselves. `PATCH`/`PUT settings` were extended beyond the minimum to also record durable audit events, matching "audited for administrative operations." `DELETE /profiles/{id}` was deliberately designed to take `expected_version` as a query parameter, not a JSON body — a real friction point (`httpx.TestClient.delete()` does not accept `json=`) found and fixed during this session's own smoke testing, and a more broadly HTTP-client-compatible choice regardless.

**Idempotency and concurrency.** `PATCH`/`DELETE`/`merge` require `expected_version`, giving retries a clear `409` rather than silent double-application. Candidate approval reuses M8's already-idempotent `CandidatePromoter.promote()`, verified in this session: a repeated approve call on an already-`PROMOTED` candidate returns the same profile with no further writes. A dedicated `Idempotency-Key` deduplication store for non-naturally-idempotent operations is a documented gap.

**WebSocket live events.** `events_live` is a simple 1-second poll-and-diff over `RecognitionEventRepository.list_recent()`, not a true pub/sub event bus — ARCHITECTURE.md describes an in-process event bus as the initial target, but wiring one is deferred; this poll-based implementation still delivers a working, token-authenticated live stream, verified in this session (successful connection with a correct token, rejection with an incorrect one).

**Background worker (`api/worker.py`).** `PipelineWorker` is a deliberately simple threaded polling loop — open source, read frame, run detect→track→quality→align→embed→recognize→enroll→select-active-user→learn-preferences, sleep, repeat — explicitly *not* the bounded-queue, multi-worker architecture ARCHITECTURE.md §5 describes as the eventual target; adaptive frequency, batching, and queue backpressure are explicitly M15's job. It still provides real `pause()`/`resume()`/`stop()` and a `WorkerHealth` snapshot the `/status` route reports. It is disabled (returns `None`) unless `camera.enabled` is true, matching the established disabled-by-default pattern; it has only been smoke-tested conceptually via its constituent parts, never run against a live camera in this session.

**A real cross-thread SQLite bug was found and fixed during this session's own smoke testing**: the M6 database connection was created in one thread but accessed from the ASGI request-handling thread(s) and would be accessed from `PipelineWorker`'s background thread, which Python's `sqlite3` module rejects by default (`check_same_thread=True`). Fixed by setting `check_same_thread=False` in `database/connection.py`'s `open_database()`, gated on verifying `sqlite3.threadsafety == 3` (fully serialized mode) at open time — this repository's actual compiled SQLite reports mode 3, so cross-thread access is safe at the C library level, not merely assumed safe. A dedicated per-thread-connection or queued-writer model remains a documented follow-up for M15.

## Consequences

- The full CRUD lifecycle (create outside the API via repository, then patch/merge/export/import/delete through the API; candidate list/approve-idempotent/reject/delete through the API) was verified end-to-end against a real temporary SQLite database using `TestClient` — no real network port was opened during verification.
- Rate limiting, the health-endpoint auth/rate-limit exemption, and WebSocket auth (accept with a correct token, disconnect with an incorrect one) were all verified.
- `face-profile serve` blocks on `uvicorn.run()`; it was not run as a live, long-lived process in this session (appropriate for a sandboxed agent session) — only `create_app()` plus `TestClient` were exercised, which cover the same application code without leaving a server listening.
- M13 (optional UI) is purely a client of this API and must add no domain logic of its own. M15 owns replacing the worker's threaded loop with the bounded-queue architecture and the event bus.
