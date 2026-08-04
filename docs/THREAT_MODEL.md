# M2 Threat Model

## Scope

This document covers the M2 camera and multi-face-detection boundaries and records threats that later milestones must resolve before identity recognition, biometric persistence, or network APIs are enabled. M2 can decode explicitly configured local media, open an explicitly enabled webcam, and process one frame through an explicitly enabled detector, but does not identify people, persist profiles, retain frames by default, or modify host settings.

## Assets

- Future face crops, embeddings, profile identifiers, and candidate records.
- User preference data and device-setting history.
- Configuration, authentication material, audit events, and model artifacts.
- Camera availability and correct active-user decisions.
- The M13 dashboard's single latest annotated webcam-preview JPEG (ephemeral,
  in-memory only; distinct from the face crops/embeddings above, which are
  never included in it).

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
| Spoofed or replayed face input | Unauthorized enrollment or activation | Candidate quarantine, manual approval by default; automatic mode additionally requires explicit operator acknowledgement, all qualification gates, and enabled liveness (currently unevaluated, not production-grade) | M8/M14 |
| Unlicensed or tampered model | Legal or supply-chain exposure | Provenance, license, checksum, compatibility matrix | M5 |
| Malformed detector output | Invalid geometry, crashes, or unsafe downstream decisions | Shape, dtype, finite-value, confidence, and frame-intersection validation; controlled errors | M2 onward |
| Detection metadata disclosure | Face-location privacy loss | Count-only logs; no boxes or landmarks in structured diagnostics | M2 onward |
| Debug-overlay disclosure | Sensitive derived-image disclosure | Explicit output only; owner-only mode and no-follow frame writer | M2 onward |
| Detector backend detail leakage | Model-path or runtime-information disclosure | Stable domain errors that omit paths and backend exception text | M2 onward |
| Plaintext biometric persistence | Irreversible privacy breach | Encryption at rest, key separation, retention and deletion policy | M6 |
| Identity instability | Wrong settings applied | Conservative thresholds, hysteresis, UNKNOWN state, atomic switching | M7/M10 |
| Unauthorized API or WebSocket access | Profile/data compromise | Loopback default; authentication, authorization, audit, rate limits | M12 |
| Unsafe host-setting writes | Availability or usability loss | Mock-first adapters, validation, bounded values, platform-specific tests | M9/M9B |
| POSIX permission assumptions on Windows | Key, database, sidecar, or debug-frame disclosure | Protected NTFS DACLs restricted to the current process-token user and LocalSystem; protected inheritance for sensitive directories; reparse-point rejection; fail-closed verification | M17 |
| Partial Windows profile application | Volume or brightness changed without the matching profile value | Probe both capabilities and prior values before mutation; reject the whole profile when either capability is unavailable; rollback and report if a later write fails | M17 |
| Queue or worker exhaustion | Denial of service | Bounded queues, timeouts, cancellation, overload metrics | M1 onward |
| Webcam preview frame disclosure | Anyone with a bearer token (or any client, when the deployment runs without one on loopback) can view whoever is currently in front of the camera | Disabled by default in every tracked config except `config/jetson-full.yaml`; same bearer-auth dependency as every other route, no URL/query token; `Cache-Control: no-store`; bounded JPEG quality/FPS/max-width; single-slot in-memory snapshot, never written to disk or logs | M13 |
| Hostile display/candidate name drawn into the preview | Oversized or control-character name inflates the annotated frame or corrupts the overlay | Preview label text is length-bounded and printable-only before any `cv2.putText` call | M13 |

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

## M13 Webcam Preview Invariants

- `ui.webcam_preview_enabled` defaults to False and is only True in the tracked
  `config/jetson-full.yaml`; every other tracked config keeps it False.
- `GET /api/v1/preview/latest.jpg` depends on the same `require_auth` bearer
  dependency as every other route and accepts no URL or query token.
- The response always carries `Cache-Control: no-store, no-cache,
  must-revalidate`.
- The worker holds at most one encoded JPEG at a time, always replaced, never
  appended to; drawing always happens on a copy of the pipeline's frame, so
  the source frame used for detection/recognition is never mutated.
- Display names and candidate temporary/UUID text are stripped to printable
  characters and length-bounded before any drawing call.
- No preview frame, pixel data, or JPEG bytes are ever written to disk or to
  a log record; only aggregate worker health (frame counts, state) is logged,
  exactly as before this feature.
- Increases this deployment's privacy exposure and network load relative to a
  preview-disabled deployment: an authenticated dashboard client can now see
  a near-live (bounded-rate) view of whoever the camera currently observes,
  and each connected client polls the daemon repeatedly instead of only on
  demand.

## M17 Windows Platform Invariants

- `settings.backend: windows` is rejected off Windows, and `linux` is rejected
  off Linux; the mock backend remains the portable safe default.
- Windows full-profile settings require both Core Audio volume and WMI
  brightness. An external-monitor-only system commonly lacks the latter and
  fails before either value is mutated.
- Windows key, database, sidecar, and debug-frame protection relies on protected
  NTFS DACL behavior that must still be verified on a native NTFS host.
- `config/windows-safe.yaml` never opens a camera, loads a model, creates the
  profile database, binds a listener, or calls pycaw/WMI.
- Tracked Windows configs remain loopback-only and contain no credentials.
- PowerShell launchers are foreground processes and make no Windows Service or
  reboot-survival claim.

## Review Triggers

Review this threat model whenever a milestone adds a camera backend, model, persistent store, network listener, authentication mechanism, real settings adapter, or new sensitive log field.
