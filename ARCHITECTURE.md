# Architecture

## 1. Purpose

This document defines the architecture of the Face Profile Recognition System. The system is a headless-first local identity and personalization service with an optional UI.

## 2. Architectural Priorities

In priority order:

1. Correct identity decisions
2. Privacy and safe biometric-data handling
3. Headless reliability
4. Testability without physical hardware
5. Separation of concerns
6. Explainable state transitions
7. Performance
8. Extensibility

## 3. System Context

```text
                    ┌──────────────────┐
                    │ USB/IP Camera(s) │
                    └────────┬─────────┘
                             │ frames
                             ▼
┌──────────────────────────────────────────────────────┐
│              Face Profile Headless Service           │
│                                                      │
│ Camera → Vision → Matching → Presence → Settings     │
│                     │                                │
│                     ├── Profile database             │
│                     ├── Event bus                    │
│                     └── REST/WebSocket API           │
└───────────────┬──────────────────────┬───────────────┘
                │                      │
                ▼                      ▼
             CLI tools              Optional UI
```

## 4. Component Boundaries

### 4.1 Camera Layer

Responsibilities:

- Open and close frame sources
- Read frames
- Attach timestamps and source identifiers
- Report camera health
- Reconnect according to policy

Must not:

- Detect faces
- Access profile records
- Apply settings

M1 implementations are `ImageFrameSource`, `VideoFrameSource`, `WebcamFrameSource`, and `MockFrameSource`. They emit `uint8` NumPy-backed `Frame` values, expose immutable `CameraHealth` snapshots, and follow explicit open/read/close lifecycle semantics. Webcam recovery is bounded by configuration. Saving is separate and explicit; no source persists frames automatically. OpenCV is isolated behind a narrow capture protocol so file and webcam failure behavior can be tested without hardware.

### 4.2 Vision Layer

Subcomponents:

- Face detector
- Landmark extractor
- Face aligner
- Quality evaluator
- Embedding generator
- Liveness evaluator
- Tracker adapter

Inputs:

- Frames

Outputs:

- Detections
- Tracks
- Quality results
- Embeddings
- Liveness results

Must not:

- Perform database writes directly
- Select the active user
- Apply settings

M2 defines an immutable, profile-independent `FaceDetector` boundary. `FaceDetection` contains a clipped `BoundingBox`, normalized confidence, and five named landmarks; detections are ordered deterministically by confidence and position. `YuNetFaceDetector` translates OpenCV `FaceDetectorYN` rows and rejects malformed, non-finite, invalid-confidence, non-intersecting, or over-limit backend output. `MockFaceDetector` supplies deterministic zero/one/multiple cases without biometric fixtures.

No detector model is bundled or selected as a production default in M2. The factory reads an explicitly configured local artifact once, verifies that exact byte buffer against its SHA-256, and passes the verified buffer directly to OpenCV, preserving both the integrity boundary and M5 provenance decision gate. Debug rendering copies the frame, draws only the M2 metadata, and persists only through the explicit private frame-output boundary.

M3 adds an in-memory `FaceTracker` boundary that wraps immutable M2 detections in `TrackedFace` results rather than modifying detection contracts. `GeometricFaceTracker` assigns process-local, non-reused temporary integer IDs for one frame source. It associates detections only when they satisfy both predicted-centroid and IoU gates, expires tracks after a configured bounded gap, and retains only a hard-bounded sequence of timestamps, boxes, and confidence values. `create_tracker()` enforces the disabled-by-default configuration boundary. It retains no pixels, crops, landmarks, embeddings, profile IDs, or durable state. Geometric continuity is not identity proof; quality, embeddings, recognition, persistence, and active-user behavior remain in later milestones.

M15 adds `predict_only()`: motion-prediction-only track advancement without a new detection, used by `api/worker.py`'s adaptive detection frequency (`detection_interval_frames`) to skip the detector on some frames while keeping tracks alive. Predicted geometry is never passed to quality/alignment/embedding/recognition — only `update()`'s real detections are.

M4 adds `HeuristicQualityEvaluator`, `SimilarityFaceAligner`, and `BestSampleTracker`. The quality evaluator scores size, sharpness, exposure, pose, and an occlusion-geometry proxy against a `QualityConfig`, and reports every failing check as a reason rather than only the first. The aligner warps accepted crops to the published InsightFace/ArcFace five-point 112x112 reference template for the M5 embedding adapter. `BestSampleTracker` is the first component that retains pixel data beyond one frame: it keeps at most one accepted, quality-ranked crop per track, bounded by a configured track count, entirely in process memory, discarded on `discard()` or process exit. `create_quality_evaluator()` and `create_aligner()` follow the same disabled-by-default factory pattern as M2/M3. The occlusion proxy is a documented geometric heuristic, not a trained occlusion or anti-spoof model; M14 owns that gap.

M5 adds `embedding.py` and `threshold_evaluation.py`. `FaceEmbedding` carries model name/version/checksum/dimension/dtype with its normalized vector; `cosine_similarity()` refuses to compare embeddings from incompatible models or dimensions. `MockEmbeddingGenerator` derives a deterministic vector from a content hash for hardware- and model-free operation; `OnnxEmbeddingGenerator` runs an integrity-verified ONNX model through OpenCV's DNN module, reusing M2's exact-byte SHA-256 gate rather than adding a new inference dependency. `threshold_evaluation.evaluate_thresholds()` computes FAR/FRR per swept threshold, per-class statistics with confidence intervals, and an equal-error-rate estimate from caller-supplied labeled pairs only — it does not fabricate accuracy evidence. `EmbeddingConfig.similarity_threshold`/`similarity_margin` record the milestone's required configurable initial values for M7 to consume; they are documented as unevaluated defaults pending a real dataset and a rights-reviewed model artifact.

### 4.3 Profile Domain

Responsibilities:

- Profile lifecycle
- Candidate lifecycle
- Embedding ownership
- Profile merge rules
- Naming and metadata
- Recognition-decision aggregation

M8 implements candidate lifecycle with `database/candidate_repository.py`'s `CandidateRepository` (schema migration v2: `candidates`, `candidate_embeddings`, encrypted exactly like `face_embeddings`) and `enrollment/manager.py`'s `CandidateManager`, which maps each track to at most one in-flight candidate. `enrollment/qualification.py`'s pure `evaluate_candidate_qualification()` implements HERMES's unknown-person policy checks (sample count, duration, quality, near-frontal, internal consistency, temporal variability, duplicate-vs-profile, duplicate-vs-candidate) before a candidate may reach `READY_FOR_REVIEW`. `enrollment/promotion.py`'s `CandidatePromoter` is the atomic, auditable, idempotent promotion transaction, re-checking duplicates immediately before creating a profile. Manual review remains the default; explicitly safety-gated automatic mode delegates to the same promoter after qualification and is enabled only by the opt-in full Jetson configuration.

M14 adds `liveness/`: `passive.py`'s `HeuristicPassiveLivenessEvaluator` (FFT high-frequency energy ratio and specular-highlight-spread heuristics — classical signal-processing proxies, not a trained model), `active_challenge.py`'s deterministic yaw-sequence turn-challenge verifier, and `depth.py`'s interface-only `DepthIRSignal` stub. "Failed liveness cannot create a permanent profile" is enforced twice: `CandidateManager` tracks a sticky per-track liveness-failed flag that permanently blocks `READY_FOR_REVIEW`, and `CandidatePromoter` independently re-checks a persisted `liveness_passed` candidate-metadata flag before promoting, rejecting (and deleting embeddings) if it is not explicitly true.

M15 adds `recognition/embedding_cache.py`'s `ActiveProfileEmbeddingCache`, amortizing the per-call active-profile embedding decrypt cost across a configured TTL rather than paying it every `recognize()` call, wired into `KnownPersonRecognizer` as an optional (backward-compatible) parameter. `database/repository.py`'s `add_embedding()` gained a `max_embeddings` storage limit. `api/routes.py`'s `GET /api/v1/metrics` exposes a small, authenticated, privacy-safe JSON snapshot of worker throughput and aggregate profile/candidate counts. FAISS was deliberately not adopted at this project's current data scale (ADR 0020).

Entities:

- Profile
- FaceEmbedding
- Candidate
- RecognitionObservation
- RecognitionDecision

M7 implements the recognition-decision-aggregation half of this responsibility with `recognition/decision.py` and `recognition/matcher.py`: `find_nearest_profiles()` ranks each profile's best-similarity embedding against a query, and `decide()` applies the documented threshold/margin rule to produce a single-observation `RecognitionDecision`. `recognition/recognizer.py`'s `KnownPersonRecognizer` is the only M7 component that reads `ProfileRepository`, loading only `ACTIVE` profiles' embeddings. Candidate lifecycle, embedding ownership beyond storage, merge rules, and naming remain M6/M8 concerns this milestone does not touch.

### 4.4 Presence Domain

Responsibilities:

- Track who is currently present
- Confirm identity over time
- Expire presence sessions
- Select the active profile
- Prevent rapid active-profile switching

M7 implements the per-track "confirm identity over time" piece with `recognition/cache.py`'s `RecognitionCache`: bounded, in-memory, per-track state that requires a configured run of consistent observations before confirming an identity, and stays sticky through transient unknown/ambiguous frames so a track's own identity does not flicker. This is distinct from M10's system-wide active-user selection, which chooses one active profile across multiple simultaneously-present, already-recognized tracks.

M10 implements "select the active profile" and "prevent rapid active-profile switching" with `presence/active_user.py`'s `ActiveUserSelector`: a stateful policy scoring each `CONFIRMED_MATCH` track (`presence/builder.py`'s `build_presence_candidate()` excludes everything else) on the ARCHITECTURE §10 weighted formula, retaining the current active profile until a challenger exceeds `switch_margin` continuously for `stability_duration_seconds`, and further gating any switch behind a `switch_cooldown_seconds` cooldown. A `LEAVING` grace period absorbs a momentary disappearance of the active track before clearing to `NONE`.

### 4.5 Settings Domain

Responsibilities:

- Define portable device-setting models
- Validate setting values
- Apply settings through OS-specific adapters
- Read current settings where supported
- Debounce preference updates
- Prevent feedback loops

M9 implements the portable model, validation, and OS-adapter layers. `settings/__init__.py`'s `SettingsAdapter` protocol gains `capabilities()` (`AdapterCapabilities`/`CapabilityStatus`: available/unsupported/permission-denied) so gaps are reported explicitly rather than silently ignored. `settings/linux.py`'s `LinuxSettingsAdapter` is the one explicitly selected real adapter (ALSA `amixer` for volume, sysfs `/sys/class/backlight` for brightness), fully dependency-injected via `CommandRunner`/`BacklightAccessor` protocols so automated tests never touch real hardware; a partial apply (one setting succeeds, the other fails) triggers a best-effort rollback of the value that changed. `settings/rate_limit.py`'s `RateLimitedSettingsAdapter` wraps any adapter to reject rapid repeated applies and record self-applied values — the mechanism ARCHITECTURE.md §11 describes; M11 owns the attribution/debounce policy built on top of it. `settings/factory.create_settings_adapter()` always returns a working (at minimum mock) adapter, but only selects the real backend when `settings.backend: linux` is explicitly configured.

M11 implements "debounce preference updates" and "prevent feedback loops" with `settings/preference_learning.py`'s `PreferenceLearner`, applying HERMES.md's four attribution rules (single active profile, minimum active duration, debounce stability, self-application suppression) before ever treating an observed device-setting value as a new user preference. `settings/last_used_service.py`'s `LastUsedPreferenceService` is the only M11 component that writes to M6, persisting an accepted change to `profile_settings` and a durable `SettingsChanged` event in one transaction.

### 4.6 Persistence Layer

Responsibilities:

- Profile persistence
- Embedding persistence
- Settings persistence
- Recognition-event persistence

M6 implements this layer with the `database/` package: SQL migrations (`schema.py`) creating `profiles`, `face_embeddings`, `profile_settings`, and `recognition_events` in a `schema_version`-tracked SQLite database; field-level AES-256-GCM encryption of embedding vectors (`crypto.py`) with a separated local key file (`keys.py`); a hardened connection lifecycle (`connection.py`, `0700`/`0600` permissions, WAL, foreign keys); and typed repositories (`repository.py`) for profile CRUD with optimistic concurrency, embedding storage, atomic merge (including re-keying moved embeddings' AEAD associated data), retention-based soft delete/purge, and encrypted export/import. `create_profile_database()` follows the disabled-by-default factory pattern established since M1; the profile CLI fails closed with `database_disabled` otherwise. The domain layer depends only on these repository classes, never on SQL or the connection directly. Repository write methods do not commit their own transaction (an M8 correction, so multi-step operations like candidate promotion can compose several writes into one atomic commit); `merge()` and `import_profile()` remain self-committing since each is itself one atomic unit. Migration v2 (M8) adds `candidates`/`candidate_embeddings`.
- Migrations
- Transactions

The domain layer depends on repository interfaces, not on a specific database implementation.

### 4.7 Event Bus

Use an in-process event bus initially. Events should be immutable and typed.

Separate transient telemetry from durable domain events. Frame, queue, and performance telemetry may be dropped according to an explicit overload policy. Profile creation, promotion, merge, deletion, consent, and settings state changes must be durable. Durable events must be written atomically with their state change using a transactional outbox or equivalent mechanism.

Events support:

- Logging
- WebSocket publishing
- Metrics
- Persistence
- Loose coupling between modules

### 4.8 API Layer

Responsibilities:

- REST resources
- WebSocket event stream
- Input validation
- Authentication and authorization for every non-loopback deployment
- API versioning, pagination, idempotency, rate limits, and optimistic concurrency
- Error translation

Must not contain domain decisions.

M12 implements this layer with `api/`: FastAPI `app.py` wires M6–M11 into one process; `routes.py` implements HERMES's full `/api/v1` surface plus `WS /events/live` (poll-based, not true pub/sub); `auth.py`/`rate_limit.py` provide bearer-token auth and fixed-window rate limiting; `worker.py`'s `PipelineWorker` is a simple threaded polling loop (not yet the bounded-queue architecture §5 describes, deferred to M15) running detect→track→quality→align→embed→recognize→enroll→select-active-user→learn-preferences when `camera.enabled`. `APIConfig` fails closed at configuration-load time for non-loopback binding without an explicit token.

M13 adds `GET /api/v1/preview/latest.jpg`, an authenticated (same bearer dependency, no URL/query token), versioned snapshot endpoint returning the pipeline worker's single latest annotated JPEG when `ui.webcam_preview_enabled` is set. It maps disabled/no-worker/no-frame-yet to distinct 404/409/503 responses (see `routes.py`'s docstring) and always sets `Cache-Control: no-store`.

### 4.9 UI Layer

Responsibilities:

- Display state
- Manage profiles and candidates
- Change settings through APIs
- Show service health and events

The UI must remain replaceable.

M13's dashboard additionally polls the preview endpoint on a bounded client-side timer (started after Connect/Refresh, paused while the tab is hidden, never overlapping in-flight requests) and renders the returned JPEG through a revoked-on-replace object URL. All annotation (boxes, known/unknown/liveness-failed color, display-name/UUID or candidate-name/UUID text) happens server-side in `PipelineWorker`; the dashboard draws nothing and makes no recognition or enrollment decision, preserving HERMES.md's "UI is a plain API client" rule.

## 5. Runtime Processes

Start with one service process containing separate asynchronous workers:

- Camera worker
- Detection and tracking worker
- Recognition worker
- Candidate enrollment worker
- Presence and active-user worker
- Settings worker
- API server
- Event persistence worker

Use bounded queues between high-throughput stages. Dropping stale frames is preferable to accumulating unbounded latency.

Model inference and blocking camera or OS operations must not execute directly on the API event loop. Blocking camera drivers use controlled threads; CPU-bound inference uses a bounded executor or worker process; GPU inference uses a bounded scheduling queue. Every queue must define capacity, overflow behavior, metrics, and shutdown semantics. Worker failures must affect health reporting and be supervised according to a documented restart policy.

## 6. Suggested Domain Models

```python
@dataclass(frozen=True)
class Frame:
    source_id: str
    sequence: int
    captured_at: datetime
    image: NDArray[np.uint8]

@dataclass(frozen=True)
class FaceDetection:
    bounding_box: BoundingBox
    confidence: float
    landmarks: FaceLandmarks

@dataclass(frozen=True)
class FaceQuality:
    accepted: bool
    score: float
    reasons: tuple[str, ...]

@dataclass(frozen=True)
class RecognitionDecision:
    state: RecognitionState
    profile_id: UUID | None
    similarity: float | None
    second_best_similarity: float | None
    reason: str
```

Large image arrays should not be copied unnecessarily between stages.

## 7. Data Model

### profiles

- id: UUID primary key
- display_name
- status
- priority
- is_owner
- metadata_json
- created_at
- updated_at
- last_seen_at
- merged_into_profile_id
- enrollment_review_status
- retention_expires_at, optional
- optimistic_version

### face_embeddings

- id: UUID primary key
- profile_id
- model_name
- model_version
- model_checksum
- embedding_dimension
- numeric_dtype
- vector
- quality_score
- source_image_path, optional
- is_representative
- created_at

### candidates

- id: UUID primary key
- temporary_name
- status
- first_seen_at
- last_seen_at
- sample_count
- aggregate_quality
- review_status
- reviewed_by, optional
- reviewed_at, optional
- retention_expires_at
- metadata_json

### candidate_embeddings

- id
- candidate_id
- vector
- quality_score
- model_name
- model_version
- model_checksum
- embedding_dimension
- numeric_dtype
- source_camera_id
- source_track_id
- created_at

### profile_settings

- profile_id
- volume
- brightness
- additional_settings_json
- updated_at

### recognition_events

- id
- event_type
- profile_id, optional
- candidate_id, optional
- track_id, optional
- similarity, optional
- camera_id
- occurred_at
- sequence
- correlation_id
- metadata_json

All tables must define foreign keys, indexes, deletion behavior, schema constraints, and optimistic versioning where concurrent updates are possible. Important query or security fields must not be hidden only inside `metadata_json`.

## 8. Recognition Decision Logic

A recognition lookup returns several nearest matches. The final decision must consider:

- Best similarity
- Second-best similarity
- Best-versus-second margin
- Number of consistent observations
- Track quality
- Profile status
- Model version compatibility

Decision examples:

```text
best < threshold                       → UNKNOWN
best ≥ threshold and margin too small  → AMBIGUOUS
best ≥ threshold and stable over time  → KNOWN
quality rejected                       → NO_DECISION
liveness failed                        → SPOOF_REJECTED
```

## 9. Candidate Deduplication

Before promotion:

1. Search candidate embeddings against active profiles.
2. Search against recent candidate clusters.
3. Confirm internal candidate consistency.
4. Reject mixed-identity candidates.
5. Promote inside a transaction.

Candidate promotion and embedding transfer must be atomic.

## 10. Active-User Selection

Active-user selection is implemented as a stateful policy component.

Suggested score:

```text
score =
    face_size_weight * normalized_face_size
  + centre_weight * centre_proximity
  + duration_weight * visible_duration
  + confidence_weight * recognition_confidence
  + priority_weight * profile_priority
```

The current active user retains an advantage until a challenger exceeds the switch margin for the configured stability duration.

## 11. Settings Feedback-Loop Protection

When the service applies settings, it records:

- Applied values
- Profile ID
- Timestamp
- Correlation ID

A subsequent OS setting observation matching a recent self-applied value must not be saved as a new user preference.

## 12. Error Handling

Errors must be classified as:

- Recoverable operational errors
- Configuration errors
- Data-integrity errors
- Model errors
- Hardware errors
- Programmer errors

The camera worker should retry recoverable disconnections. Database-integrity errors should fail loudly and avoid partial writes.

## 13. Security and Privacy

- Treat face images and embeddings as sensitive biometric data.
- Store only the minimum number of samples required.
- Prefer local processing.
- Restrict file and database permissions.
- Do not expose raw camera streams by default. The M13 annotated webcam
  preview (`ui.webcam_preview_enabled`) is not a raw stream -- it is a
  bounded-rate, server-drawn, single-latest-frame JPEG snapshot, disabled by
  default, authenticated the same as every other route, and off in every
  tracked config except `config/jetson-full.yaml`.
- Protect administrative API operations.
- Log identifiers rather than full biometric vectors.
- Provide export and deletion operations.
- Define configurable retention policies.
- Encrypt permanent embeddings, retained images, exports, temporary artifacts, SQLite sidecar files, and backups according to the selected threat model.
- Keep representative face-image storage disabled by default.
- Store encryption keys separately from encrypted data and document rotation and recovery.
- Require explicit owner review and approval for candidate promotion by default.
- Require authentication and administrator authorization for every non-loopback administrative API.
- Bind to loopback by default and fail closed when a non-loopback deployment lacks production security configuration.
- Audit profile export, import, promotion, merge, deletion, and security-setting changes.

## 14. Deployment

Supported deployment targets may include:

- Linux systemd service
- macOS launchd agent
- Windows service
- Container deployment when camera and host-setting access are available

The initial production target should be selected before implementing OS-specific settings adapters.

M17 adds a native Windows 10/11 x64 **source-execution** target. Windows
capture is selected deterministically through OpenCV DirectShow; host settings
remain behind `SettingsAdapter` and use pycaw/Core Audio plus WMI internal-panel
brightness. Selecting a real settings backend on the wrong OS fails in the
factory before construction. The Linux ALSA/sysfs adapter remains unchanged and
available on Linux.

Sensitive local files use one cross-platform boundary: POSIX mode enforcement
on Linux and protected NTFS DACLs on Windows. The Windows directory DACL carries
inheritable child ACEs so SQLite sidecars inherit the same current-token-user +
LocalSystem restriction. Real NTFS behavior is not considered verified until the
evidence in `docs/reports/M17.md` is updated from a native Windows run.

`run.ps1` and `run-continuous.ps1` are foreground launchers, not a Windows
Service implementation. The latter provides bounded restart backoff but no
auto-start, logoff survival, or reboot-survival guarantee.

Development may run without authentication only when explicitly configured for loopback-only binding. Deployments exposed beyond loopback require transport protection, authentication, authorization, rate limiting, and secure secret management.

## 15. Architecture Decision Records

Major decisions should be recorded under `docs/adr/` using short ADR files, including:

- Recognition model selection
- Database selection
- Vector search strategy
- UI framework
- Liveness approach
- Encryption and retention policy
- API authentication, authorization, and audit policy
- Model provenance and licensing
- Worker execution and queue overload policy
- Windows platform security, settings, and native launch path (ADR 0021)
