# ADR 0003: Biometric Encryption and Retention Boundary

- Status: Accepted
- Date: 2026-07-30
- Decision milestone: M6

## Context

Face crops and embeddings are sensitive, difficult to revoke, and unnecessary before persistent profiles are introduced.

## Decision

Biometric persistence begins only behind the M6 storage boundary. Sensitive records must be encrypted at rest with key material separated from the database, support schema migration and auditable deletion, and have explicit retention limits for samples, candidates, exports, and backups. Logs and events contain identifiers rather than biometric payloads.

## Consequences

Earlier milestones keep samples bounded and ephemeral. M6 cannot complete without encryption, deletion, retention, backup, and migration tests.
