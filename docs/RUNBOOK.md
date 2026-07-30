# Operations Runbook

## Supported Foundation

M1 uses CPython 3.11.15 and uv 0.11.32 as its exact baseline. CI also verifies CPython 3.12.13. Project metadata accepts maintained CPython 3.11–3.12 patch releases. The default command performs a hardware-free lifecycle check. It does not activate a camera, persist biometric data, or change host settings.

## Bootstrap

```bash
uv sync --locked --all-groups
```

## Validate Configuration and Lifecycle

```bash
uv run face-profile --config config/default.yaml check
```

A successful run exits with code `0` and writes `ServiceStarted` and `ServiceStopped` JSON events to standard output.

## Configure a Camera Source

Camera access remains disabled unless `camera.enabled` is explicitly set to `true`. Supported `source` values are `mock`, `image`, `video`, and `webcam`.

Image or video files require `camera.path`:

```yaml
camera:
  enabled: true
  source: image
  path: tests/fixtures/camera/synthetic.png
  device_index: 0
  retry_attempts: 2
```

Webcams use `device_index` and bounded `retry_attempts`; `path` must remain `null`. The `check` command opens and closes the configured source but does not retain a frame.

To verify deterministic frame reading and saving without camera hardware:

```bash
uv run python -c "from pathlib import Path; from face_profile.camera import ImageFrameSource, save_frame; source=ImageFrameSource(Path('tests/fixtures/camera/synthetic.png')); source.open(); save_frame(source.read(), Path('/tmp/face-profile-m1.png')); source.close()"
```

The output path is explicit. The service does not save frames by default.

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

### Camera source unavailable

The default configuration intentionally disables hardware. For file sources, verify `path` exists and is a supported image or video. For webcams, verify `device_index`, host permissions, and exclusive device access. Startup fails closed with exit `3` if the source cannot open.

### Dependency drift

Run `uv lock --check`. If dependency changes are intentional, regenerate `uv.lock`, review the complete diff, and rerun all quality gates.

## Security Notes

- Never log frames, embeddings, candidate images, tokens, passwords, or arbitrary request payloads.
- Never commit `.env` files or runtime data.
- Use only synthetic, consented, or otherwise authorized media. Never commit real face fixtures.
- Treat future biometric exports and backups as sensitive encrypted material.
- Review `docs/THREAT_MODEL.md` before introducing a new external boundary.
