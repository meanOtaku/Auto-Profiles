# Operations Runbook

## Supported Foundation

M0 uses CPython 3.11.15 and uv 0.11.32 as its exact baseline. CI also verifies CPython 3.12.13. Project metadata accepts maintained CPython 3.11–3.12 patch releases. The default command performs a hardware-free lifecycle check. It does not activate a camera, persist biometric data, or change host settings.

## Bootstrap

```bash
uv sync --locked --all-groups
```

## Validate Configuration and Lifecycle

```bash
uv run face-profile --config config/default.yaml check
```

A successful run exits with code `0` and writes `ServiceStarted` and `ServiceStopped` JSON events to standard output.

Expected startup failures are written as JSON to standard error:

- Exit `2`: missing or invalid configuration.
- Exit `3`: service resource startup failure.
- Exit `4`: service resource shutdown failure.

## Quality Gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest
uv run pip-audit
uv run pip-licenses --from=mixed --ignore-packages face-profile-system --fail-on=UNKNOWN --format=plain
uv build
```

## Troubleshooting

### Configuration rejected

Validate YAML syntax, remove unknown keys, and ensure booleans and integers use their native YAML types. Do not put credentials in the configuration file.

### Camera adapter missing

The default configuration intentionally disables hardware. Setting `camera.enabled: true` before a real frame source is wired causes startup to fail closed.

### Dependency drift

Run `uv lock --check`. If dependency changes are intentional, regenerate `uv.lock`, review the complete diff, and rerun all quality gates.

## Security Notes

- Never log frames, embeddings, candidate images, tokens, passwords, or arbitrary request payloads.
- Never commit `.env` files or runtime data.
- Treat future biometric exports and backups as sensitive encrypted material.
- Review `docs/THREAT_MODEL.md` before introducing a new external boundary.
