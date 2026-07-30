# HERMES.md

# Face Profile Recognition System

## Project Overview

Build a production-quality facial recognition service that identifies multiple people, creates persistent profiles for unknown people, remembers profile-specific device preferences, and restores those preferences when a saved person becomes the active user.

The project must be designed as a **headless service first**. A graphical UI is optional and must communicate with the headless service through documented APIs.

## Primary Goals

The system must:

- Detect and recognize multiple faces in the same image or video stream.
- Maintain a unique profile for every person.
- Create temporary candidates for unknown people.
- Promote candidates to permanent profiles only after repeated, consistent, high-quality observations.
- Store multiple face embeddings per profile.
- Remember settings such as volume and display brightness.
- Restore the active profile's saved settings.
- Run completely without a UI.
- Expose REST, WebSocket, and CLI interfaces.
- Remain modular, testable, and suitable for long-term development.

## Product Modes

### Headless Mode — Primary

The service runs in the background and performs all camera, recognition, profile, settings, and persistence operations.

```text
Camera
  ↓
Headless recognition service
  ├── REST API
  ├── WebSocket events
  ├── CLI
  └── Optional UI client
```

### UI Mode — Secondary

The UI is an API client. It must not contain recognition, profile matching, enrollment, active-user selection, or OS-settings business logic.

## Recommended Technology Stack

- Python 3.11 or 3.12
- InsightFace
- SCRFD face detector
- ArcFace face embeddings
- ONNX Runtime
- OpenCV
- FastAPI
- SQLite initially
- PostgreSQL as an optional future backend
- NumPy cosine similarity initially
- FAISS or another vector index at larger scale
- Pydantic settings and YAML configuration
- pytest
- Ruff
- mypy or Pyright
- Alembic or an equivalent migration system

Verify the licensing terms of all pretrained face-recognition models before commercial deployment.

## High-Level Pipeline

```text
Camera frame
  ↓
Detect all faces
  ↓
Track faces across frames
  ↓
Evaluate face quality
  ↓
Align accepted faces
  ↓
Generate normalized embeddings
  ↓
Match against saved profiles
  ├── Known → confirm identity → update presence
  └── Unknown → candidate enrollment workflow
  ↓
Select active user
  ↓
Load and apply active profile settings
  ↓
Emit events and persist state
```

## Core Design Rules

1. Headless mode is the primary product.
2. The UI is replaceable and optional.
3. Camera, vision, profile, presence, settings, persistence, API, and UI code must remain independent.
4. Unknown people must not become permanent profiles from one frame.
5. Every permanent profile must have a UUID.
6. A profile may contain multiple embeddings and selected representative samples.
7. Recognition decisions must use configurable thresholds.
8. Thresholds must be evaluated on local test data rather than copied blindly.
9. Multiple people may be recognized simultaneously, but only one may be active for shared device settings.
10. Hardware and OS integrations must provide mock implementations for automated tests.
11. Raw face images and biometric embeddings must be treated as sensitive data.
12. All state changes must be observable through logs and events.

## Unknown-Person Policy

An unknown detection creates or updates a temporary candidate.

A candidate may become permanent only after all configured criteria pass, including:

- Minimum sample count
- Minimum observation duration
- Minimum face-quality score
- Embedding consistency
- At least one near-frontal sample
- No reliable match with an existing profile
- No likely match with another active candidate
- Liveness approval when liveness is enabled

Suggested starting defaults:

```yaml
enrollment:
  enabled: true
  minimum_samples: 5
  minimum_observation_seconds: 3
  minimum_quality: 0.65
  candidate_expiry_seconds: 30
  maximum_embeddings_per_profile: 20
  default_name_prefix: Unknown
```

Generated names should follow a predictable pattern such as `Unknown-000001`.

## Known-Person Policy

A known identity should be confirmed over multiple observations. The system must avoid identity flicker and must distinguish these states:

- unprocessed
- possible match
- confirmed match
- ambiguous
- unknown
- rejected because of quality
- rejected because of liveness

Use both:

- A minimum best-match threshold
- A minimum margin between the best and second-best matches

## Multiple-Person and Active-User Policy

Recognition and active-user selection are different concerns.

The system may report:

```text
Present profiles: Vaibhav, Rahul, Unknown-000007
Active profile: Vaibhav
```

Suggested active-user factors:

- Face size
- Distance from frame centre
- Continuous visibility duration
- Recognition confidence
- Profile priority
- Optional gaze or screen-attention signal

Recommended initial rule:

> Select the recognized person with the largest stable face near the centre of the frame for at least two seconds.

Use hysteresis and a switch cooldown so a brief appearance does not change active settings.

## Profile Data

Each profile should store:

- UUID
- Display name
- Status
- Created, updated, and last-seen timestamps
- Priority
- Owner or guest flag
- Metadata
- Multiple normalized embeddings
- Embedding quality scores
- Optional representative face images
- Preferred settings
- Recognition history
- Merge and deletion audit data

Suggested profile states:

- candidate
- active
- disabled
- merged
- deleted

## Settings System

Recognition code must never directly control operating-system settings.

Provide a common interface and separate implementations:

```text
SettingsAdapter
  ├── MockSettingsAdapter
  ├── WindowsSettingsAdapter
  ├── LinuxSettingsAdapter
  └── MacOSSettingsAdapter
```

Initial settings:

- Volume
- Screen brightness

Potential future settings:

- Audio output device
- Microphone
- Theme
- Keyboard layout
- Wallpaper
- Application layout
- Monitor configuration

A setting should be saved as a user's last-used preference only when attribution is reliable. Suggested rules:

- Exactly one active profile exists.
- The profile has been active for a configured minimum duration.
- The changed value remains stable for a debounce period.
- The value was not just applied by the service itself.

## State Machines

### Face Track State

```text
NEW
  ↓
TRACKING
  ↓
QUALITY_PENDING
  ├── REJECTED
  └── ACCEPTED
        ↓
     MATCHING
        ├── KNOWN
        ├── AMBIGUOUS
        └── UNKNOWN
```

### Candidate Enrollment State

```text
CREATED
  ↓
COLLECTING
  ├── EXPIRED
  ├── REJECTED
  └── READY_FOR_REVIEW
        ↓
     PROMOTED
```

### Active User State

```text
NONE
  ↓
CANDIDATE_ACTIVE
  ↓
ACTIVE
  ↓
LEAVING
  ↓
NONE
```

## Suggested Repository Structure

```text
face-profile-system/
├── apps/
│   ├── daemon/
│   ├── cli/
│   └── ui/
├── src/
│   └── face_profile/
│       ├── camera/
│       ├── vision/
│       ├── profiles/
│       ├── presence/
│       ├── settings/
│       ├── api/
│       ├── database/
│       ├── events/
│       └── config.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── sample_images/
│   └── sample_videos/
├── config/
├── data/
├── scripts/
├── HERMES.md
├── ARCHITECTURE.md
├── CONTRIBUTING.md
├── CODING_STANDARDS.md
├── TESTING.md
├── ROADMAP.md
├── pyproject.toml
└── README.md
```

## Required Interfaces

### Camera

```python
class FrameSource:
    def open(self) -> None: ...
    def read(self) -> "Frame": ...
    def close(self) -> None: ...
```

Required implementations:

- Webcam
- Video file
- Static image
- Mock source

### Settings

```python
class SettingsAdapter:
    def read_current(self) -> "DeviceSettings": ...
    def apply(self, settings: "DeviceSettings") -> "ApplyResult": ...
    def validate(self, settings: "DeviceSettings") -> None: ...
```

### Profile Repository

The repository layer must support:

- Create
- Read
- Update
- Disable
- Soft delete
- Add and remove embeddings
- Merge profiles
- Export and import
- Query recent candidates

## Required API Surface

```text
GET    /health
GET    /status
GET    /profiles
GET    /profiles/{id}
PATCH  /profiles/{id}
DELETE /profiles/{id}
POST   /profiles/{id}/merge
GET    /events
GET    /settings/current
PUT    /profiles/{id}/settings
POST   /system/pause
POST   /system/resume
WS     /events/live
```

## Required CLI Surface

```text
face-profile camera test
face-profile detect
face-profile compare
face-profile recognize
face-profile evaluate-threshold
face-profile profile create
face-profile profile list
face-profile profile show
face-profile profile delete
face-profile profile merge
face-profile serve
```

## Event Types

At minimum:

- CameraStarted
- CameraStopped
- CameraFailed
- FaceDetected
- FaceLost
- TrackStarted
- TrackEnded
- ProfilePossibleMatch
- ProfileRecognized
- ProfileAmbiguous
- CandidateCreated
- CandidateUpdated
- CandidateExpired
- ProfileCreated
- ProfileMerged
- ProfileActivated
- ProfileDeactivated
- SettingsApplied
- SettingsChanged
- LivenessPassed
- LivenessFailed

## Milestones

### M0 — Project Scaffolding

Create the project, configuration, logging, test infrastructure, mock camera, and mock settings adapter.

Acceptance:

- Application starts without a camera.
- `pytest`, `ruff check .`, and type checking pass.

### M1 — Camera Input

Implement webcam, image, video, and mock frame sources.

Acceptance:

- Frames can be read and saved.
- Video termination and camera errors are handled cleanly.

### M2 — Multi-Face Detection

Detect all faces and return boxes, landmarks, and confidence.

Acceptance:

- Zero, one, and multiple-face inputs work.
- Detection remains independent from profile logic.

### M3 — Face Tracking

Assign temporary track IDs across frames.

Acceptance:

- IDs remain reasonably stable.
- Recognition does not run on every frame unnecessarily.

### M4 — Quality Filtering and Alignment

Reject small, blurry, dark, overexposed, strongly rotated, and heavily occluded samples.

Acceptance:

- Every rejection includes a reason.
- Best accepted crop is retained per track.

### M5 — Embeddings and Similarity

Generate normalized embeddings and implement similarity comparison.

Acceptance:

- Same-person and different-person test sets are evaluated.
- Threshold evaluation reports false accepts and false rejects.

### M6 — Persistent Profile Database

Implement profile and embedding persistence with migrations.

Acceptance:

- Profiles survive restarts.
- One profile can contain multiple embeddings.

### M7 — Known-Person Recognition

Match tracks to profiles using threshold, margin, and temporal confirmation.

Acceptance:

- Multiple known people are recognized independently.
- Unknown people remain unknown.
- Identity does not flicker rapidly.

### M8 — Automatic Unknown Enrollment

Create temporary candidates, collect consistent observations, prevent duplicates, and promote qualified candidates.

Acceptance:

- A one-frame face never creates a permanent profile.
- Two simultaneous unknown people remain separate.
- Poorly observed known people do not immediately create duplicates.

### M9 — Settings Abstraction

Implement the settings interface and mock adapter before real OS adapters.

Acceptance:

- Recognition tests never modify real device settings.
- Invalid values are rejected.

### M10 — Active-User Selection

Implement stable active-user scoring, hysteresis, and cooldown.

Acceptance:

- Only one profile controls shared settings.
- Brief appearances do not switch active users.

### M11 — Last-Used Preference Persistence

Read and save stable user changes with attribution and debounce safeguards.

Acceptance:

- Self-applied changes are not treated as new user input.
- Changes are attributed only to the active profile.

### M12 — Headless Daemon and API

Run the complete system without a graphical window.

Acceptance:

- Profiles persist.
- Health reporting works.
- UI disconnection has no effect.
- The service can start automatically after reboot.

### M13 — Optional UI

Create dashboard, profile management, candidate review, settings editing, and live events.

Acceptance:

- Every operation uses the service API.
- Closing the UI does not stop the system.

### M14 — Liveness and Spoof Resistance

Introduce temporal checks, passive anti-spoofing, and optional active challenges.

Acceptance:

- Static photo attacks are flagged in the standard test set.
- Failed liveness cannot create a permanent profile.

### M15 — Performance and Scale

Optimize detection frequency, tracking, batching, caching, and vector search.

Acceptance targets:

- At least 15 processing FPS on the selected target hardware, or a documented hardware-specific target.
- Recognition confirmation within two seconds under normal conditions.
- No profile creation from one-second appearances.
- Low duplicate-profile rate in the standard scenario suite.

## AI Agent Operating Rules

The AI agent must:

1. Work on exactly one milestone at a time.
2. Inspect the repository and read all project documentation before editing.
3. Never skip a milestone dependency.
4. Avoid implementing future milestones early unless a minimal interface is required.
5. Keep modules typed and independently testable.
6. Add or update tests with every behavior change.
7. Use mock cameras and mock settings adapters in automated tests.
8. Never require physical hardware for unit tests.
9. Never create a permanent profile from one observation.
10. Never hardcode recognition thresholds without configuration and evaluation documentation.
11. Never put business logic in the UI.
12. Preserve backward compatibility unless a migration is explicitly documented.
13. Run the full test, lint, and type-check suites before completion.
14. Update documentation and configuration examples.
15. Produce a milestone report with summary, files changed, commands run, test results, manual verification, limitations, and known issues.
16. Stop after the assigned milestone.
17. Report failures honestly and do not fabricate test results.

## Standard Milestone Prompt

```text
Implement Milestone <NUMBER>: <NAME>.

Read HERMES.md, ARCHITECTURE.md, CONTRIBUTING.md,
CODING_STANDARDS.md, TESTING.md, and ROADMAP.md before coding.

Project principles:
- Headless mode is primary.
- The UI is an optional API client.
- Business logic must not depend on the UI.
- Hardware and OS integrations require mock implementations.
- Multiple faces may be present simultaneously.
- Unknown detections begin as temporary candidates.
- Permanent profiles require repeated quality-approved observations.
- Profiles use UUIDs and may contain multiple embeddings.
- Only the selected active profile may control shared device settings.

Before coding:
1. Inspect the repository.
2. Identify affected modules and tests.
3. Describe migration and compatibility risks.
4. State the milestone acceptance criteria.

During implementation:
1. Keep modules small and typed.
2. Add unit and integration tests.
3. Add structured error handling.
4. Use configuration instead of magic constants.
5. Preserve all passing behavior from previous milestones.

Before completing:
1. Run the complete test suite.
2. Run linting and type checking.
3. Perform the milestone manual test.
4. Update documentation.
5. Provide an exact test report.

Do not begin the next milestone.
```

## Definition of Done

A milestone is complete only when:

- Acceptance criteria pass.
- Automated tests pass.
- Linting passes.
- Type checking passes.
- Documentation is updated.
- Configuration examples are updated.
- Manual verification is documented.
- No known regression is hidden.

## Final Objective

When a person appears in front of the device, the completed system should:

1. Detect and track the person.
2. Assess sample quality and liveness.
3. Identify the person or manage them as an unknown candidate.
4. Maintain a unique and reviewable profile.
5. Select the active user deterministically.
6. Restore that profile's settings.
7. Learn stable preference changes safely.
8. Continue operating without any UI.
9. Expose clean APIs for desktop, web, mobile, and automation clients.
