# ADR 0006: M1 Camera Input Boundary

- Status: Accepted
- Date: 2026-07-30

## Context

M1 requires webcam, image, video, and mock frame sources that share deterministic lifecycle and health behavior. Automated verification must not depend on camera hardware or real face data. File decoding and webcam capture require a local media backend, while server and CI environments must remain headless.

## Decision

- Use the exact locked `opencv-python-headless` package for local image/video decoding, frame encoding, and webcam capture.
- Represent decoded frames as NumPy `uint8` arrays and exclude image payloads from `Frame.repr`.
- Keep camera activation disabled in the shipped configuration.
- Select sources through strict configuration: file paths only for image/video, device indexes only for webcam behavior, and bounded reconnect attempts.
- Expose immutable, controlled health snapshots rather than raw backend exceptions.
- Treat video end-of-stream separately from a read failure before the backend's reported frame count.
- Persist a frame only through an explicit `save_frame` call and caller-selected destination.
- Verify file adapters with versioned geometric fixtures containing no face or biometric data. Verify webcam recovery with injected capture doubles, not host hardware.

## Consequences

- OpenCV and NumPy become runtime dependencies and must remain locked, audited, and license-inventoried.
- OpenCV includes native media decoders; operators should use trusted, authorized local inputs and keep the dependency current.
- CI proves adapter behavior but does not claim that a physical webcam, driver, permission model, resolution, frame rate, or target-hardware throughput has been validated.
- No detector, face processing, automatic retention, network streaming, or performance claim is introduced in M1.
