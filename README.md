# Face Profile Recognition System

A headless-first, local identity and device-personalization service built incrementally with privacy-preserving defaults. The project is currently at **M1: Camera Input**; no facial recognition, biometric persistence, network API, or real operating-system adapter is enabled yet.

## M1 Capabilities

- Strict immutable YAML configuration with unknown-key rejection.
- Hardware-disabled startup by default.
- Newline-delimited JSON logs with an explicit context allowlist.
- Webcam, image, video, and deterministic mock frame sources.
- Immutable camera-health snapshots and bounded webcam reconnect attempts.
- Explicit frame saving with controlled encoder failures.
- Versioned synthetic image/video fixtures containing no biometric data.
- Protocol boundaries and deterministic mocks for device settings.
- Explicit service lifecycle states with fail-closed resource startup and shutdown.
- A hardware-free CLI lifecycle check.
- Locked dependencies and CI gates for linting, formatting, typing, tests, audits, secret scanning, license metadata, and package builds.

## Quick Start

Exact baseline: CPython 3.11.15 and uv 0.11.32. CI also verifies CPython 3.12.13 compatibility. Project metadata accepts maintained CPython 3.11 and 3.12 patch releases, while the committed lockfile and exact development-tool pins make the verified toolchain reproducible.

```bash
uv sync --locked --all-groups
uv run face-profile --config config/default.yaml check
```

A successful check emits `ServiceStarted` and `ServiceStopped` JSON events and exits with status `0`. It does not access a camera or change host settings.

## Development Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest
uv run pip-audit
uv run pip-licenses --from=mixed --ignore-packages face-profile-system --fail-on=UNKNOWN --format=plain
uv build
```

## Repository Guide

- `HERMES.md` — primary implementation contract and milestone rules
- `ARCHITECTURE.md` — components, boundaries, data flow, and runtime design
- `ROADMAP.md` — milestone sequence and completion criteria
- `CODING_STANDARDS.md` — Python and architecture conventions
- `TESTING.md` — test strategy and quality gates
- `CONTRIBUTING.md` — development and review workflow
- `docs/THREAT_MODEL.md` — security boundaries, threats, and required controls
- `docs/RUNBOOK.md` — bootstrap, validation, and troubleshooting procedures
- `docs/adr/` — accepted architectural decisions

## Safety and Privacy

Do not commit real face images, embeddings, credentials, profile databases, or runtime exports. Camera access remains disabled until a frame source is explicitly configured. Future non-loopback APIs must fail closed unless authentication is configured.

## Development Policy

Implement exactly one milestone at a time. A milestone is complete only after its automated gates pass, the documented manual verification is performed, and the roadmap is synchronized with the implementation.
