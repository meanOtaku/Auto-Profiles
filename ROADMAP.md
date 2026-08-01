# Roadmap

## Roadmap Principles

- Headless functionality precedes UI work.
- Safety precedes convenience in unknown enrollment.
- Mock adapters precede real hardware and OS integration.
- Every milestone must be independently testable.
- Do not proceed until acceptance criteria are met or explicitly waived and documented.

## Phase 1 — Foundation

### M0: Project Scaffolding

Deliver:

- Python package
- Configuration system
- Structured logging
- Test setup
- Mock camera
- Mock settings adapter
- CI checks
- Exact Python and tooling baseline
- Threat model and secure deployment defaults
- Initial ADRs for model selection, encryption/retention, API security, and worker execution

Exit criteria:

- Service skeleton starts.
- Tests, linting, and type checking pass.
- Loopback-only defaults and fail-closed production configuration are documented.

### M1: Camera Sources

Deliver:

- Webcam source
- Image source
- Video source
- Mock source
- Camera health reporting

Exit criteria:

- Deterministic frame acquisition tests pass.
- Temporary failures are handled.

### M2: Multi-Face Detection

Deliver:

- Detector adapter
- Bounding boxes
- Confidence
- Landmarks
- Debug output

Exit criteria:

- Zero, one, and multiple-face cases pass.

### M3: Tracking

Deliver:

- Tracker adapter
- Stable temporary track IDs
- Track lifecycle
- Bounded track-sample retention using basic metadata

Exit criteria:

- Recorded crossing and occlusion scenarios pass at the documented quality level.

### M4: Quality and Alignment

Deliver:

- Blur, size, exposure, pose, and occlusion checks
- Alignment
- Rejection reasons
- Quality-scored best-frame selection

Exit criteria:

- Poor samples are excluded from enrollment and matching.

### M5: Embeddings and Evaluation

Deliver:

- Embedding adapter
- Normalization
- Cosine similarity
- Threshold-evaluation script
- Model provenance, license, checksum, dimension, dtype, and compatibility record

Exit criteria:

- Local dataset report exists.
- Configurable initial threshold and margin are selected.

## Phase 2 — Identity

### M6: Persistent Profiles

Deliver:

- Profile schema
- Embedding schema
- Settings schema
- Event schema
- Repositories
- Migrations
- CRUD CLI
- Encryption-at-rest and key-management integration
- Retention and secure-deletion behavior
- Encrypted export/import and backup/restore policy

Exit criteria:

- Profiles and embeddings survive restart.
- Permanent biometric data, database sidecars, temporary artifacts, and exports follow the documented encryption and retention policy.

### M7: Known-Person Recognition

Deliver:

- Nearest-match search
- Threshold and margin decision
- Temporal confirmation
- Recognition cache per track

Exit criteria:

- Multiple known identities are independently recognized.
- Ambiguous results are not forced into a known identity.

### M8: Candidate Enrollment

Deliver:

- Candidate lifecycle
- Sample collection
- Consistency checks
- Duplicate prevention
- Promotion transaction
- Candidate CLI/API review
- Minimum temporal spoof and replay checks
- Manual owner approval by default

Exit criteria:

- One-frame enrollment is impossible.
- Simultaneous unknown users remain separate.
- Promotion is atomic.
- Printed-photo and replay scenarios cannot bypass approval and liveness gates.
- Automatic promotion remains disabled unless all production gates are explicitly enabled and tested.

## Phase 3 — Personalization

### M9: Settings Abstraction and Initial Platform Adapter

Deliver:

- Portable settings model
- Validation
- Mock adapter
- Application logs and events
- One selected OS and desktop-environment adapter
- Capability and permission detection
- Safe ranges, rate limits, feedback-loop protection, and rollback or fail-safe behavior

Exit criteria:

- All specified automated settings behavior is covered using mocks and never changes host settings.
- Manual tests pass for the selected real platform adapter.

### M10: Active-User Manager

Deliver:

- Scoring policy
- Stability duration
- Switch margin
- Cooldown
- Presence integration

Exit criteria:

- Brief challengers do not change settings.
- Only one active profile exists.

### M11: Last-Used Settings

Deliver:

- Current-setting observation
- Debounce
- Attribution rules
- Feedback-loop suppression
- Settings history

Exit criteria:

- Stable manual changes persist to the correct profile.

## Phase 4 — Productization

### M12: Headless Daemon and API

Deliver:

- Long-running service
- REST API
- WebSocket stream
- CLI administration
- Health and status
- Pause and resume
- Graceful shutdown
- Service installation documentation
- Versioned `/api/v1` surface
- Authentication, authorization, administrative roles, and audit records
- Rate limits, request-size limits, idempotency, and optimistic concurrency
- Secure non-loopback deployment configuration

Exit criteria:

- Full operation without a UI.
- Restart preserves profiles and settings.
- Non-loopback startup fails closed without production security configuration.

### M13: Optional UI

Deliver:

- Dashboard
- Profile pages
- Candidate review
- Merge and delete operations
- Settings editing
- Live event display
- Optional debug preview

Exit criteria:

- UI closure has no effect on the daemon.
- UI contains no domain logic.

## Phase 5 — Safety and Scale

### M14: Advanced Liveness

Deliver in stages:

1. Strengthen the minimum temporal checks introduced in M8
2. Passive anti-spoof model
3. Optional active challenge
4. Optional depth or IR support

Exit criteria:

- Standard printed-photo tests are rejected at a documented rate.
- Failed liveness prevents permanent enrollment.

### M15: Performance and Scale

Deliver:

- Adaptive detection frequency
- Tracking between detections
- Batched inference
- Recognition caching
- Storage limits
- FAISS or equivalent when justified
- Metrics dashboard or export

Exit criteria:

- Hardware-specific FPS and latency targets pass.
- Duplicate profile rate is within the documented target.

## Post-M15 Backlog

Potential future work:

- Multi-camera fusion
- Profile synchronization across trusted devices
- Mobile companion application
- Plugin system for new settings
- Voice recognition as a secondary signal
- Wearable or BLE presence signals
- Gaze-aware active-user selection
- Workspace and application restoration
- Multi-monitor profiles
- Home-automation integration
- Federated or privacy-preserving profile synchronization
- Audit export and long-term archival policy

## Release Milestones

### Prototype Release

Includes M0–M5.

Purpose:

- Validate camera, detection, tracking, quality, and embedding choices.

### Identity Alpha

Includes M0–M8.

Purpose:

- Persistent known recognition and conservative, owner-reviewed unknown enrollment.

### Headless Beta

Includes M0–M12.

Purpose:

- Complete primary product without UI.

### UI Beta

Includes M13.

Purpose:

- Administrative and monitoring interface.

### Production Candidate

Includes M14–M15 plus security, privacy, packaging, and deployment review.

## Milestone Status Table

| Milestone | Name | Status | Evidence |
|---|---|---|---|
| M0 | Project scaffolding | Complete | [M0 report](docs/reports/M0.md): 29 tests; Ruff, mypy, locked build, clean-wheel CLI smoke test, dependency/license audits, and secret scan passed on 2026-07-30. |
| M1 | Camera sources | Complete | [M1 report](docs/reports/M1.md): 59 tests pass on Python 3.11.15 and 3.12.13; local quality, audit, packaging, privacy, secret-scanning, and independent-review gates passed on 2026-07-30. |
| M2 | Multi-face detection | Complete | [M2 report](docs/reports/M2.md): 87 tests pass on Python 3.11.15 and 3.12.13; quality, model-integrity, privacy, audit, packaging, secret-scanning, and final independent-review gates passed on 2026-07-30. |
| M3 | Tracking | Implementation drafted; verification deferred | [M3 report](docs/reports/M3.md): in-memory geometric tracking, temporary IDs, bounded metadata retention, and configuration were added on 2026-08-01. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M4 | Quality and alignment | Implementation drafted; verification deferred | [M4 report](docs/reports/M4.md): heuristic size/blur/exposure/pose/occlusion-proxy quality evaluator, ArcFace-template similarity alignment, and bounded per-track best-crop retention were added on 2026-08-01. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M5 | Embeddings and evaluation | Implementation drafted; verification deferred | [M5 report](docs/reports/M5.md): normalized-embedding generation (mock content-hash and integrity-gated ONNX/OpenCV-DNN backends), cosine similarity, and a threshold-evaluation module/CLI were added on 2026-08-01. No real accuracy dataset or rights-reviewed model exists yet; automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M6 | Persistent profiles | Implementation drafted; verification deferred | [M6 report](docs/reports/M6.md): SQL migrations, AES-256-GCM field-level embedding encryption with separated key storage, hardened file permissions, profile/embedding/settings/event repositories (including a merge AEAD re-keying fix found during smoke testing), retention/purge, encrypted export/import, and profile CRUD CLI were added on 2026-08-01. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M7 | Known-person recognition | Implementation drafted; verification deferred | [M7 report](docs/reports/M7.md): nearest-profile matching, threshold/margin decision, and a per-track temporal-confirmation cache with flicker-resistant sticky confirmation were added on 2026-08-01, along with a `recognize` CLI command. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M8 | Candidate enrollment | Implementation drafted; verification deferred | [M8 report](docs/reports/M8.md): candidate lifecycle, qualification (consistency, temporal-variability, duplicate, near-frontal checks), and atomic/auditable/idempotent owner-reviewed promotion were added on 2026-08-01, along with a prerequisite M6 repository atomicity fix and a `candidate` CLI. `automatic_promotion` is unconditionally rejected pending M12/M14 gates. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M9 | Settings abstraction and initial platform adapter | Implementation drafted; verification deferred | [M9 report](docs/reports/M9.md): capability/permission reporting, a fully dependency-injected Linux (ALSA + sysfs backlight) adapter with partial-apply rollback, and a rate-limiting/self-application-recording wrapper were added on 2026-08-01. The HERMES-required real-hardware manual test has not been performed (no ALSA mixer or backlight device in this environment) and is not claimed as passed. Automated tests and full quality gates were explicitly deferred by the owner. |
| M10 | Active-user manager | Implementation drafted; verification deferred | [M10 report](docs/reports/M10.md): weighted scoring, switch-margin/stability-duration hysteresis, switch cooldown, and a leaving grace period were added on 2026-08-01, restricted to M7-confirmed tracks only. A floating-point precision bug at the exact switch-margin boundary was found and fixed during smoke testing. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M11 | Last-used settings | Implementation drafted; verification deferred | [M11 report](docs/reports/M11.md): PreferenceLearner (attribution, debounce, self-application/feedback-loop suppression) and LastUsedPreferenceService (persistence + durable SettingsChanged event) were added on 2026-08-01 and verified end-to-end against a real temporary M6 database. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M12 | Headless daemon and API | Implementation drafted; verification deferred | [M12 report](docs/reports/M12.md): full FastAPI /api/v1 surface (profiles/candidates/events/settings/system + WebSocket live events), bearer auth with fail-closed non-loopback validation, rate limiting, and a threaded pipeline worker were added on 2026-08-01, verified end-to-end via TestClient. A cross-thread SQLite bug and a DELETE-body API design defect were found and fixed. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M13 | Optional UI | Implementation drafted; verification deferred | [M13 report](docs/reports/M13.md): a static, dependency-free dashboard that is purely an /api/v1 client (no domain logic) was added on 2026-08-01, disabled by default. Automated tests and full quality gates were explicitly deferred by the owner and have not been claimed. |
| M14 | Advanced liveness | Implementation drafted; verification deferred | [M14 report](docs/reports/M14.md): passive spectral/reflectance heuristics, an active-challenge yaw-sequence verifier, and a depth/IR interface stub were added on 2026-08-01, with two-point enforcement (sticky per-track gating plus promotion-time defense-in-depth) that "failed liveness cannot create a permanent profile" — verified against a real database including a simulated bypass attempt. No standard printed-photo test set or trained anti-spoof model exists in this environment; no accuracy rate is claimed. Automated tests and full quality gates were explicitly deferred by the owner. |
| M15 | Performance and scale | Implementation drafted; verification deferred | [M15 report](docs/reports/M15.md): a TTL-refreshed active-profile embedding cache, adaptive detection frequency with motion-prediction tracking between detections, per-profile embedding storage limits, and a JSON metrics endpoint were added on 2026-08-01. FAISS was deliberately not added (unjustified at current data scale, see ADR 0020). No real FPS/latency benchmark exists in this environment; no number is claimed. Automated tests and full quality gates were explicitly deferred by the owner. |
