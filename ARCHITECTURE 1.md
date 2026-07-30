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

### 4.3 Profile Domain

Responsibilities:

- Profile lifecycle
- Candidate lifecycle
- Embedding ownership
- Profile merge rules
- Naming and metadata
- Recognition-decision aggregation

Entities:

- Profile
- FaceEmbedding
- Candidate
- RecognitionObservation
- RecognitionDecision

### 4.4 Presence Domain

Responsibilities:

- Track who is currently present
- Confirm identity over time
- Expire presence sessions
- Select the active profile
- Prevent rapid active-profile switching

### 4.5 Settings Domain

Responsibilities:

- Define portable device-setting models
- Validate setting values
- Apply settings through OS-specific adapters
- Read current settings where supported
- Debounce preference updates
- Prevent feedback loops

### 4.6 Persistence Layer

Responsibilities:

- Profile persistence
- Embedding persistence
- Settings persistence
- Recognition-event persistence
- Migrations
- Transactions

The domain layer depends on repository interfaces, not on a specific database implementation.

### 4.7 Event Bus

Use an in-process event bus initially. Events should be immutable and typed.

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
- Authentication when enabled
- Error translation

Must not contain domain decisions.

### 4.9 UI Layer

Responsibilities:

- Display state
- Manage profiles and candidates
- Change settings through APIs
- Show service health and events

The UI must remain replaceable.

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

### face_embeddings

- id: UUID primary key
- profile_id
- model_name
- model_version
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
- metadata_json

### candidate_embeddings

- id
- candidate_id
- vector
- quality_score
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
- metadata_json

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
- Do not expose raw camera streams by default.
- Protect administrative API operations.
- Log identifiers rather than full biometric vectors.
- Provide export and deletion operations.
- Define configurable retention policies.

## 14. Deployment

Supported deployment targets may include:

- Linux systemd service
- macOS launchd agent
- Windows service
- Container deployment when camera and host-setting access are available

The initial production target should be selected before implementing OS-specific settings adapters.

## 15. Architecture Decision Records

Major decisions should be recorded under `docs/adr/` using short ADR files, including:

- Recognition model selection
- Database selection
- Vector search strategy
- UI framework
- Liveness approach
- Encryption and retention policy
