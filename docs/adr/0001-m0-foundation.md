# ADR 0001: M0 Foundation and Secure Defaults

- Status: Accepted
- Date: 2026-07-30

## Context

The project needs a runnable foundation that does not depend on a camera, machine-learning runtime, database, or operating-system integration. It must preserve the architecture's replaceable boundaries while making unsafe behavior opt-in.

## Decision

- Use CPython 3.11.15 and uv 0.11.32 as the exact development baseline, and verify CPython 3.12.13 compatibility in CI. Project metadata accepts maintained CPython 3.11 and 3.12 patch releases.
- Use a `src/` package layout and `uv.lock` for reproducible environments.
- Use Pydantic v2 for strict immutable configuration models and PyYAML for input parsing.
- Use Python protocols for frame-source and settings-adapter boundaries.
- Ship deterministic in-memory mocks before real hardware adapters.
- Keep camera access disabled in the default configuration.
- Emit newline-delimited JSON logs with an explicit context allowlist.
- Expose a `face-profile ... check` command that exercises a complete startup and shutdown lifecycle without hardware.
- Use exact development-tool pins, Ruff, mypy strict mode, pytest, dependency and license checks, secret scanning, package builds, and commit-pinned GitHub Actions as merge gates.

## Consequences

- M0 can run and be tested on ordinary development and CI hosts.
- No biometric or operating-system behavior is implied by the foundation.
- Frame payload typing is deliberately minimal in M0 and will be refined with the M1 camera contract.
- The first real target-platform adapter remains a later M9B decision and must not be inferred from the CI operating system.
