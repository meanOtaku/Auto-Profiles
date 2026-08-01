# ADR 0008: Bounded Geometric Tracking Boundary

- Status: Accepted
- Date: 2026-08-01
- Decision milestone: M3

## Context

M3 must assign stable temporary identifiers across detection frames, tolerate bounded occlusion, and retain only bounded metadata. Detection remains a profile-independent M2 contract. M3 must not prematurely introduce recognition, embeddings, alignment, image retention, persistence, enrollment, or host-setting behavior.

## Decision

Define an in-memory `FaceTracker` protocol and a `GeometricFaceTracker` implementation. The tracker accepts an M1 `Frame` and immutable M2 `FaceDetection` values, and returns an immutable `TrackedFace` wrapper. It never mutates M2 detections and never treats a temporary track ID as a persistent identity.

Track IDs are monotonically allocated integers, scoped to one tracker process and source, and never reused by that tracker. The tracker rejects mixed sources, negative sequence values, and non-increasing frame sequences. `create_tracker()` is the configuration boundary and returns no tracker until `tracking.enabled` is explicitly true. Association is deterministic and geometric: each accepted association must pass both predicted-centroid distance and IoU gates. This handles ordinary motion and provides a documented, hardware-free basis for crossing and short occlusion scenarios without using appearance-based re-identification.

The only retained history is a bounded list of `TrackSample` records: sequence, capture time, bounding box, and confidence. It excludes image pixels, crops, landmarks, embeddings, identity, and persistent storage. Configuration supplies hard bounds for active tracks, retained samples, association gates, and missing-frame expiry.

## Consequences

- M2 detections remain reusable independently of tracking.
- M3 data is transient and privacy-minimized; it is discarded when tracks end or the process stops.
- Track ID continuity is best-effort geometric continuity, not identity proof. Crossing and occlusion quality remains limited by detection cadence and geometry.
- M4 may consume bounded track metadata for quality decisions, but owns image-quality scoring and best-frame selection.
- M5/M7 own embeddings and identity decisions; M6 owns durable storage.
