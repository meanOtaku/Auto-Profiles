# Testing Strategy

## 1. Goals

Testing must prove that the system:

- Handles zero, one, and multiple faces
- Recognizes known people reliably
- Avoids false permanent enrollment
- Prevents duplicate profiles
- Selects one stable active user
- Applies and saves settings safely
- Runs without a UI
- Recovers from operational failures

## 2. Test Layers

### Unit Tests

Test pure logic and individual adapters with mocks.

Examples:

- Quality thresholds
- Similarity and margin decisions
- Candidate-promotion rules
- Active-user scoring
- Settings debounce
- Feedback-loop suppression
- Configuration validation

### Integration Tests

Test combinations of components using deterministic fixtures.

Examples:

- Video source → detector → tracker
- Embedding store → matcher
- Candidate manager → profile repository
- Presence manager → mock settings adapter
- API → application service → test database

### End-to-End Tests

Run the headless service against recorded scenarios.

Examples:

- Known user enters and settings load
- Two known users appear
- Unknown user becomes a candidate
- Candidate is promoted
- User leaves and returns
- Service restarts and profiles persist

### Manual Hardware Tests

Use real cameras and OS adapters only after mock-based tests pass.

## 3. Required Fixtures

Maintain versioned, privacy-safe fixtures for:

- No-face images
- Single-face images
- Multi-face images
- Small faces
- Blurry faces
- Low-light faces
- Side profiles
- Occluded faces
- Recorded crossing tracks
- Temporary camera failure
- Known versus unknown identity samples
- Printed-photo spoof tests

Do not commit personal biometric datasets without explicit authorization.

## 4. Determinism

- Seed random operations.
- Mock time for duration and cooldown tests.
- Use recorded frames rather than live cameras in CI.
- Pin model versions in reproducible test environments.
- Use tolerances for floating-point comparisons.

## 5. Recognition Evaluation

The threshold-evaluation script should report:

- Genuine-pair score distribution
- Impostor-pair score distribution
- False accept rate
- False reject rate
- Equal error rate where useful
- Best-versus-second margin statistics
- Results by camera or lighting scenario

Do not declare a universal threshold without dataset-specific evaluation.

## 6. Candidate Enrollment Scenarios

Required automated scenarios:

1. Unknown person appears for one frame.
2. Unknown person remains for sufficient duration.
3. Two unknown people appear simultaneously.
4. Same unknown person leaves and returns.
5. Known person appears under poor lighting.
6. Candidate contains one outlier embedding.
7. Candidate closely resembles an existing profile.
8. Candidate expires before qualification.
9. Promotion is interrupted by a database failure.
10. Concurrent promotion attempts occur.

Expected safety property:

> No test may create a permanent profile from a single unconfirmed observation.

## 7. Active-User Scenarios

Test:

- One known user
- Two known users with different face sizes
- Challenger appears briefly
- Active user moves off-centre
- Active user leaves
- Unknown person remains while known person leaves
- Priority profile enters
- Cooldown prevents rapid switching

## 8. Settings Scenarios

Use `MockSettingsAdapter` in automated tests.

Test:

- Valid application
- Invalid range
- Adapter failure
- Retry policy
- Same profile reactivation
- Profile switch
- Self-applied value observed again
- User changes value and it stabilizes
- Rapid slider movement
- No active profile
- Multiple present profiles with one selected active profile

## 9. API Tests

Test:

- Schema validation
- Authentication and authorization when enabled
- Pagination
- Profile CRUD
- Candidate review
- Merge operation
- Settings update
- Pause and resume
- WebSocket events
- Destructive-operation safeguards

## 10. Database Tests

Use a temporary database.

Test:

- Migrations from every supported version
- Transaction rollback
- Foreign-key behavior
- Candidate promotion atomicity
- Profile merge
- Soft delete
- Export and import
- Model-version metadata

## 11. Performance Tests

Measure separately:

- Frame acquisition latency
- Detection latency
- Tracking latency
- Embedding latency
- Vector-search latency
- End-to-end recognition confirmation
- CPU, GPU, and memory use
- Queue depth and dropped-frame count

Performance tests must record target hardware and configuration.

## 12. Reliability Tests

Test:

- Camera disconnect and reconnect
- Corrupt frame
- Model load failure
- Database unavailable
- Disk full
- Invalid configuration
- Worker crash
- Graceful shutdown
- Restart with persisted profiles

## 13. Security and Privacy Tests

Verify:

- Embeddings are not logged
- Raw camera preview is disabled by default
- API permissions protect profile deletion and export
- Deleted profile data follows the configured retention policy
- Paths cannot escape configured storage directories
- Export files do not unintentionally contain secrets

## 14. CI Quality Gates

Minimum gates:

```bash
ruff format --check .
ruff check .
mypy src
pytest
```

Add coverage reporting after the foundational milestones. Coverage is a diagnostic, not a substitute for scenario quality.

## 15. Milestone Test Report Template

```text
Milestone:
Commit:
Environment:
Model version:
Database version:

Commands executed:
- ...

Automated results:
- Unit tests:
- Integration tests:
- End-to-end tests:
- Lint:
- Type checking:

Manual verification:
- ...

Performance observations:
- ...

Known limitations:
- ...

Failures or skipped tests:
- ...
```
