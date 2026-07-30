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

Exit criteria:

- Service skeleton starts.
- Tests, linting, and type checking pass.

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
- Best-frame retention

Exit criteria:

- Recorded crossing and occlusion scenarios pass at the documented quality level.

### M4: Quality and Alignment

Deliver:

- Blur, size, exposure, pose, and occlusion checks
- Alignment
- Rejection reasons

Exit criteria:

- Poor samples are excluded from enrollment and matching.

### M5: Embeddings and Evaluation

Deliver:

- Embedding adapter
- Normalization
- Cosine similarity
- Threshold-evaluation script

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

Exit criteria:

- Profiles and embeddings survive restart.

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

Exit criteria:

- One-frame enrollment is impossible.
- Simultaneous unknown users remain separate.
- Promotion is atomic.

## Phase 3 — Personalization

### M9: Settings Abstraction

Deliver:

- Portable settings model
- Validation
- Mock adapter
- Application logs and events

Exit criteria:

- Complete automated test coverage uses only mocks.

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

Exit criteria:

- Full operation without a UI.
- Restart preserves profiles and settings.

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

### M14: Liveness

Deliver in stages:

1. Temporal movement checks
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
- Encrypted profile storage
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
- Administrative roles and audit export

## Release Milestones

### Prototype Release

Includes M0–M5.

Purpose:

- Validate camera, detection, tracking, quality, and embedding choices.

### Identity Alpha

Includes M0–M8.

Purpose:

- Persistent known recognition and conservative unknown enrollment.

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
| M0 | Project scaffolding | Not started | |
| M1 | Camera sources | Not started | |
| M2 | Multi-face detection | Not started | |
| M3 | Tracking | Not started | |
| M4 | Quality and alignment | Not started | |
| M5 | Embeddings and evaluation | Not started | |
| M6 | Persistent profiles | Not started | |
| M7 | Known-person recognition | Not started | |
| M8 | Candidate enrollment | Not started | |
| M9 | Settings abstraction | Not started | |
| M10 | Active-user manager | Not started | |
| M11 | Last-used settings | Not started | |
| M12 | Headless daemon and API | Not started | |
| M13 | Optional UI | Not started | |
| M14 | Liveness | Not started | |
| M15 | Performance and scale | Not started | |
