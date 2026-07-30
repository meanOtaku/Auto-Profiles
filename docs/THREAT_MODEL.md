# M0 Threat Model

## Scope

This document covers the M0 foundation and records threats that later milestones must resolve before biometric data or network APIs are enabled. M0 does not capture camera frames, persist profiles, or modify host settings by default.

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
| Sensitive values in logs | Biometric or credential disclosure | JSON field allowlist, identifier-only context, adversarial logging tests | M0 onward |
| Malformed or hostile configuration | Unsafe startup | Strict schema, unknown-key rejection, controlled failure | M0 |
| Spoofed or replayed face input | Unauthorized enrollment or activation | Candidate quarantine, manual approval, replay checks, advanced liveness | M8/M14 |
| Unlicensed or tampered model | Legal or supply-chain exposure | Provenance, license, checksum, compatibility matrix | M5 |
| Plaintext biometric persistence | Irreversible privacy breach | Encryption at rest, key separation, retention and deletion policy | M6 |
| Identity instability | Wrong settings applied | Conservative thresholds, hysteresis, UNKNOWN state, atomic switching | M7/M10 |
| Unauthorized API or WebSocket access | Profile/data compromise | Loopback default; authentication, authorization, audit, rate limits | M12 |
| Unsafe host-setting writes | Availability or usability loss | Mock-first adapters, validation, bounded values, platform-specific tests | M9/M9B |
| Queue or worker exhaustion | Denial of service | Bounded queues, timeouts, cancellation, overload metrics | M1 onward |

## M0 Security Invariants

- The shipped configuration does not open a camera.
- The only settings adapter is in memory and cannot alter the host.
- Configuration models reject unknown keys and type coercion.
- Expected startup failures produce controlled machine-readable errors without tracebacks.
- Structured logs serialize only approved context fields; frames, embeddings, secrets, and arbitrary metadata are excluded.
- CI actions are pinned to immutable commit SHAs and the dependency lockfile is enforced.

## Review Triggers

Review this threat model whenever a milestone adds a camera backend, model, persistent store, network listener, authentication mechanism, real settings adapter, or new sensitive log field.
