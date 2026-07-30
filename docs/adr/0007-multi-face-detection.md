# ADR 0007: Multi-Face Detection Boundary

- Status: Accepted
- Date: 2026-07-30
- Decision milestone: M2

## Context

M2 must expose zero, one, or multiple face detections with bounding boxes, confidence, five-point landmarks, and explicit debug output. Detection must remain independent from tracking, identity, enrollment, persistence, liveness, and device settings. The accepted model-provenance decision reserves production model selection and evaluation for M5.

## Decision

Define immutable, profile-independent `Point`, `BoundingBox`, `FaceLandmarks`, and `FaceDetection` values behind a `FaceDetector` protocol.

Provide two adapters:

- `MockFaceDetector` for deterministic zero/one/multiple contract tests without biometric fixtures.
- `YuNetFaceDetector` for OpenCV `FaceDetectorYN`-compatible output. It resets input size for every frame, translates the documented row contract, validates shape, numeric values, native status, and configured cardinality, clips boxes to the frame, filters confidence, and returns a deterministic order.

The factory keeps detection disabled and mock-backed by default. Real YuNet inference requires an explicitly supplied local model and exact SHA-256. The artifact is read once, the exact bytes are verified, and those same bytes are passed through OpenCV's ONNX buffer overload so pathname replacement cannot change the loaded artifact. No detector artifact is committed or selected as a production default in M2. Model provenance, rights, quality evaluation, runtime compatibility, and production acceptance remain gated by ADR 0002 and M5.

Debug rendering copies the frame and draws only boxes, confidence, and landmarks. Persistence is explicit and uses the M1 owner-only, no-follow frame writer. Structured completion logs expose only a face count.

## Consequences

- M2 can be verified deterministically without real faces or camera hardware.
- A compatible YuNet artifact can be smoke-tested locally without weakening the model-integrity boundary.
- Detection output is reusable by M3 tracking but does not create tracks itself.
- M2 tests establish interface correctness and failure handling, not detector accuracy.
- Production packaging with a detector model remains blocked until M5 records provenance, intended-use rights, checksum, input/output contract, provider, compatibility, and evaluation results.
