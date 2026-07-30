# ADR 0002: Model Selection and Provenance Gate

- Status: Accepted
- Date: 2026-07-30
- Decision milestone: M5

## Context

Detector and embedding models affect accuracy, performance, privacy, platform support, and legal use. Architecture must not depend on an unreviewed pretrained artifact.

## Decision

Model backends remain replaceable. Before M5 accepts any model artifact, the repository must record its source, version, license terms, checksum, input/output contract, numeric precision, runtime provider, and evaluation results on approved data. Research-only pretrained weights are not production defaults.

## Consequences

M0–M4 may use mocks or explicitly licensed fixtures. Production packaging is blocked until provenance and intended-use rights are documented.
