# Face Profile Recognition System

A headless-first, local identity and device-personalization service built incrementally with privacy-preserving defaults. The project has an **M12 headless daemon and API implementation under verification**; every capability (biometric persistence, recognition, enrollment, the real settings adapter, active-user selection, preference learning, and the API/daemon itself) remains an explicit opt-in.

## M12 Headless Daemon and API Addition

- Full FastAPI `/api/v1` surface (profiles, candidates, events, settings, system pause/resume) plus a poll-based `WS /events/live`, verified end-to-end via `TestClient`.
- Bearer-token auth with fail-closed non-loopback validation at config-load time, plus fixed-window rate limiting.
- `PipelineWorker`: a threaded detect→...→learn-preferences loop with pause/resume/health, disabled unless `camera.enabled`.
- Two real bugs found and fixed during this milestone's own smoke testing: a cross-thread SQLite crash, and a `DELETE`-with-JSON-body API design defect (now a query parameter).
- `face-profile serve` CLI command.
- M12 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M11 Last-Used Preference Persistence Addition

- `PreferenceLearner` implements HERMES's four attribution rules: single active profile, minimum active duration, debounce stability, and self-application/feedback-loop suppression.
- A profile switch always restarts debounce, so a value stable under one profile is never silently credited to another.
- `LastUsedPreferenceService` persists an accepted stable change to the active profile's settings and records a durable `SettingsChanged` audit event, in one transaction — verified end-to-end against a real temporary M6 database.
- No scheduled polling loop exists yet; `observe()` is called on demand pending M12's daemon worker loop.
- M11 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M10 Active-User Selection Addition

- Weighted active-user scoring (face size, centre proximity, visible duration, recognition confidence, profile priority) over currently confirmed-recognized tracks only.
- Hysteresis: the current active profile keeps control until a challenger exceeds the switch margin continuously for a configured stability duration.
- A further switch cooldown blocks rapid re-switching even after a legitimate takeover.
- A `LEAVING` grace period absorbs a momentary track disappearance before clearing to no active user.
- A floating-point precision bug at the exact switch-margin boundary was found and fixed during this milestone's own smoke testing.
- M10 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M9 Settings Abstraction and Platform Adapter Addition

- `SettingsAdapter.capabilities()` reports available/unsupported/permission-denied per setting instead of silently ignoring gaps.
- `LinuxSettingsAdapter`: ALSA `amixer` for volume, sysfs backlight for brightness — the one explicitly selected real adapter, fully dependency-injected so automated tests never touch real hardware.
- Partial-application rollback: if one of volume/brightness fails after the other succeeded, the adapter rolls the succeeded value back and reports both failures.
- `RateLimitedSettingsAdapter`: rejects rapid repeated applies and records self-applied values for M11's future feedback-loop attribution.
- `settings.backend` stays `mock` by default; selecting `linux` is an explicit opt-in.
- The HERMES-required real-hardware manual test has **not** been performed (no ALSA mixer or backlight device exist in this environment) and is not claimed as passed.
- M9 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M8 Candidate Enrollment and Review Addition

- Temporary candidate lifecycle (`COLLECTING → {EXPIRED, REJECTED, READY_FOR_REVIEW} → PROMOTED`) with the same field-level encryption as permanent profiles.
- Qualification gates: minimum samples/duration/quality, a near-frontal sample requirement, internal-consistency (mixed-identity) and temporal-variability (minimum spoof/replay) checks, and duplicate detection against both active profiles and other candidates.
- A one-frame observation can never reach `READY_FOR_REVIEW`; rejected or expired candidates have their biometric samples deleted immediately.
- Atomic, auditable, idempotent owner-reviewed promotion (`CandidatePromoter`), including a promotion-time duplicate re-check that defends against a matching profile appearing after a candidate qualified.
- `automatic_promotion` is unconditionally rejected by configuration validation until M12/M14 deliver the required production security and liveness gates.
- `face-profile candidate list|show|approve|reject` CLI.
- A prerequisite defect in M6's repositories (auto-commit per call, incompatible with atomic multi-write promotion) was found and fixed during this milestone.
- M8 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M7 Known-Person Recognition Addition

- Nearest-profile similarity search and a threshold/margin recognition decision (`UNKNOWN`/`AMBIGUOUS`/`POSSIBLE_MATCH`) over active profiles' stored embeddings.
- A bounded, in-memory, per-track temporal-confirmation cache: a track's identity is only confirmed after repeated consistent observations, and stays sticky through a transient unknown/ambiguous frame so it does not flicker.
- Multiple simultaneously visible tracks are recognized independently; unknown people never get promoted to a known identity.
- `face-profile recognize --image PATH` CLI command.
- Recognizer loading of every active profile's embeddings on every call is a known M15 performance follow-up, not silently accepted as production-ready.
- M7 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M6 Persistent Profile Database Addition

- Encrypted (AES-256-GCM), migrated SQLite profile/embedding/settings/event storage, disabled by default.
- Embedding vectors are encrypted at the field level with a key stored separately from the database, both hardened to owner-only file permissions.
- `ProfileRepository`: CRUD with optimistic concurrency, multiple embeddings per profile, atomic merge, retention-based soft delete/purge, and encrypted export/import.
- `face-profile profile create|list|show|delete|merge` CLI, fails closed unless `database.enabled` is explicit.
- A merge-time embedding decryption bug (AEAD associated data bound to the pre-merge profile ID) was found and fixed during this milestone's own smoke testing.
- M6 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M5 Embeddings and Similarity Addition

- Normalized `FaceEmbedding` generation with a deterministic, model-free `MockEmbeddingGenerator` and an integrity-gated `OnnxEmbeddingGenerator` (OpenCV DNN, no new inference dependency).
- `cosine_similarity()` that refuses to compare embeddings from incompatible models or dimensions.
- A `threshold_evaluation` module and `evaluate-threshold` CLI command that compute FAR/FRR/EER and score statistics from a caller-supplied labeled pair dataset only — no fabricated accuracy evidence.
- A `compare` CLI command running the full detect→quality→align→embed pipeline on two images.
- No embedding model artifact is bundled; no approved biometric evaluation dataset exists in this repository.
- M5 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M4 Quality Filtering and Alignment Addition

- Heuristic size, sharpness, exposure, pose, and occlusion-proxy quality checks, each reporting a reason on rejection.
- Similarity-transform alignment to the published InsightFace/ArcFace 112x112 five-point template.
- Bounded, in-memory, per-track retention of the single best **accepted** crop; no disk persistence.
- The occlusion check is a documented landmark-geometry heuristic, not a trained occlusion or anti-spoof model.
- M4 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M3 Tracking Addition

- In-memory geometric tracker with process-local, non-reused temporary IDs.
- Deterministic association requiring both predicted-centroid and IoU gates.
- Strict single-source, monotonically increasing frame-sequence boundary.
- Configurable bounded occlusion expiry, active-track count, and metadata-only sample retention.
- No retained pixels, crops, embeddings, profile IDs, persistence, recognition, or settings behavior.
- M3 automated verification is intentionally deferred at the owner's request; this implementation is not yet a claimed completed milestone.

## M2 Capabilities

- Strict immutable YAML configuration with unknown-key rejection.
- Hardware-disabled startup by default.
- Newline-delimited JSON logs with an explicit context allowlist.
- Webcam, image, video, and deterministic mock frame sources.
- Immutable camera-health snapshots and bounded webcam reconnect attempts.
- Explicit frame saving with controlled encoder failures.
- Versioned synthetic image/video fixtures containing no biometric data.
- Protocol boundaries and deterministic mocks for device settings.
- Explicit service lifecycle states with fail-closed resource startup and shutdown.
- A hardware-free CLI lifecycle check.
- Profile-independent detector contracts for zero, one, or multiple faces.
- An OpenCV YuNet adapter that returns clipped boxes, confidence, and documented five-point landmarks in deterministic order.
- A strict SHA-256 integrity gate for explicitly supplied detector models; no model artifact is bundled or enabled by default.
- Explicit debug overlays that reuse the private, no-follow frame-output boundary.
- A one-frame `detect` CLI command with safe count-only structured output.
- Locked dependencies and CI gates for linting, formatting, typing, tests, audits, secret scanning, license metadata, and package builds.

## Quick Start

Exact baseline: CPython 3.11.15 and uv 0.11.32. CI also verifies CPython 3.12.13 compatibility. Project metadata accepts maintained CPython 3.11 and 3.12 patch releases, while the committed lockfile and exact development-tool pins make the verified toolchain reproducible.

```bash
uv sync --locked --all-groups
uv run face-profile --config config/default.yaml check
```

A successful check emits `ServiceStarted` and `ServiceStopped` JSON events and exits with status `0`. It does not access a camera or change host settings.

The `detect` command requires both camera and detection configuration. The shipped defaults keep both disabled and mock-backed. See `docs/RUNBOOK.md` for a hardware-free example and integrity-pinned YuNet setup.

## Development Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest
uv run pip-audit
uv run pip-licenses --from=mixed --ignore-packages face-profile-system --fail-on=UNKNOWN --format=plain
uv build
```

## Repository Guide

- `HERMES.md` — primary implementation contract and milestone rules
- `ARCHITECTURE.md` — components, boundaries, data flow, and runtime design
- `ROADMAP.md` — milestone sequence and completion criteria
- `CODING_STANDARDS.md` — Python and architecture conventions
- `TESTING.md` — test strategy and quality gates
- `CONTRIBUTING.md` — development and review workflow
- `docs/THREAT_MODEL.md` — security boundaries, threats, and required controls
- `docs/RUNBOOK.md` — bootstrap, validation, and troubleshooting procedures
- `docs/adr/` — accepted architectural decisions

## Safety and Privacy

Do not commit real face images, embeddings, credentials, profile databases, model artifacts without provenance review, or runtime exports. Camera and detection access remain disabled until explicitly configured. Detection geometry and debug overlays are sensitive and are not written unless an explicit output path is supplied. Future non-loopback APIs must fail closed unless authentication is configured.

## Development Policy

Implement exactly one milestone at a time. A milestone is complete only after its automated gates pass, the documented manual verification is performed, and the roadmap is synchronized with the implementation.
