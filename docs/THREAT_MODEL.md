# M2 Threat Model

## Scope

This document covers the M2 camera and multi-face-detection boundaries and records threats that later milestones must resolve before identity recognition, biometric persistence, or network APIs are enabled. M2 can decode explicitly configured local media, open an explicitly enabled webcam, and process one frame through an explicitly enabled detector, but does not identify people, persist profiles, retain frames by default, or modify host settings.

## Assets

- Future face crops, embeddings, profile identifiers, and candidate records.
- User preference data and device-setting history.
- Configuration, authentication material, audit events, and model artifacts.
- Camera availability and correct active-user decisions.

## Trust Boundaries

1. Camera or fixture input entering the frame-source interface.
2. Model artifacts entering the detector and embedder interfaces.
3. Persistent data crossing the storage boundary.
4. Commands crossing future CLI, REST, and WebSocket boundaries.
5. Device settings crossing mock or platform-adapter boundaries.
6. Logs and telemetry leaving the process.

## Threats and Required Controls

| Threat | Impact | Required control | Milestone |
|---|---|---|---|
| Unauthorized camera activation | Privacy loss | Hardware disabled by default; explicit configuration and lifecycle ownership | M0–M1 |
| Malicious or malformed media | Native decoder crash or resource exhaustion | Trusted local inputs, exact OpenCV lock, bounded tests, controlled open/read failures | M1 onward |
| Accidental frame retention | Sensitive image disclosure | No default persistence; explicit destination required; image payload excluded from representations and logs | M1 onward |
| Sensitive values in logs | Biometric or credential disclosure | JSON field allowlist, identifier-only context, adversarial logging tests | M0 onward |
| Malformed or hostile configuration | Unsafe startup | Strict schema, unknown-key rejection, controlled failure | M0 |
| Spoofed or replayed face input | Unauthorized enrollment or activation | Candidate quarantine, manual approval, replay checks, advanced liveness | M8/M14 |
| Unlicensed or tampered model | Legal or supply-chain exposure | Provenance, license, checksum, compatibility matrix | M5 |
| Malformed detector output | Invalid geometry, crashes, or unsafe downstream decisions | Shape, dtype, finite-value, confidence, and frame-intersection validation; controlled errors | M2 onward |
| Detection metadata disclosure | Face-location privacy loss | Count-only logs; no boxes or landmarks in structured diagnostics | M2 onward |
| Debug-overlay disclosure | Sensitive derived-image disclosure | Explicit output only; owner-only mode and no-follow frame writer | M2 onward |
| Detector backend detail leakage | Model-path or runtime-information disclosure | Stable domain errors that omit paths and backend exception text | M2 onward |
| Plaintext biometric persistence | Irreversible privacy breach | Encryption at rest, key separation, retention and deletion policy | M6 |
| Identity instability | Wrong settings applied | Conservative thresholds, hysteresis, UNKNOWN state, atomic switching | M7/M10 |
| Unauthorized API or WebSocket access | Profile/data compromise | Loopback default; authentication, authorization, audit, rate limits | M12 |
| Unsafe host-setting writes | Availability or usability loss | Mock-first adapters, validation, bounded values, platform-specific tests | M9/M9B |
| Queue or worker exhaustion | Denial of service | Bounded queues, timeouts, cancellation, overload metrics | M1 onward |

## M2 Security Invariants

- The shipped configuration does not open a camera.
- Camera source selection is explicit and file paths are accepted only for image/video sources.
- Webcam recovery attempts are bounded; health reports use controlled status and error codes.
- Frame saving is an explicit call with an explicit output path; the service does not retain frames by default.
- The only settings adapter is in memory and cannot alter the host.
- Configuration models reject unknown keys and type coercion.
- Expected startup failures produce controlled machine-readable errors without tracebacks.
- Structured logs serialize only approved context fields; frames, embeddings, secrets, and arbitrary metadata are excluded.
- CI actions are pinned to immutable commit SHAs and the dependency lockfile is enforced.
- Detection is disabled and mock-backed in the shipped configuration.
- YuNet configuration requires a local model path and exact lowercase SHA-256; the artifact is read once and OpenCV receives the same verified in-memory bytes, while mock configuration rejects model fields and YuNet-only controls.
- No detector model artifact is bundled, and M2 does not bypass the M5 provenance and evaluation gate.
- Detector rows must be numeric, finite, correctly shaped, confidence-bounded, positive-sized, intersect the frame, and remain within the configured `top_k` cardinality bound.
- Native, model-path, and cleanup exception details are suppressed from public error chains and structured output; cleanup failure receives a distinct controlled status.
- Detection output is profile-independent and contains no identity, tracking, enrollment, persistence, or settings behavior.
- Logs expose only the face count; pixels, boxes, landmarks, configured paths, and backend details remain excluded.
- Debug overlays are copies, never automatic, and use the private frame-output boundary.

## Review Triggers

Review this threat model whenever a milestone adds a camera backend, model, persistent store, network listener, authentication mechanism, real settings adapter, or new sensitive log field.
