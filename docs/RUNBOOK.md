# Operations Runbook

For a guided, end-to-end Ubuntu walkthrough (clone, `run.sh`, CLI usage, private local
configs, and every feature's explicit opt-in and prerequisites), see
`docs/RUNNING_ON_UBUNTU.md`. This runbook documents the underlying bootstrap/config/
troubleshooting contract in more detail.

## Supported Foundation

M2 uses CPython 3.11.15 and uv 0.11.32 as its exact baseline. CI also verifies CPython 3.12.13. Project metadata accepts maintained CPython 3.11–3.12 patch releases. The default command performs a hardware-free lifecycle check. It does not activate a camera, load a detector model, persist biometric data, or change host settings.

## Bootstrap

```bash
uv sync --locked --all-groups
```

## Single-Command Launcher (`run.sh`)

`./run.sh` at the repository root wraps the steps below for a fresh Ubuntu developer machine:

1. Resolves the repository directory from the script's own location (via `readlink -f`), so it works regardless of the caller's current directory, and refuses to run if it isn't next to this project's `pyproject.toml`.
2. Requires `uv`. If it is missing, the script prints the official installer command (`curl -LsSf https://astral.sh/uv/install.sh | sh`, a user-space install to `~/.local/bin` with no `sudo`, no `apt`, and no system configuration changes) and only runs it after explicit approval: an interactive `[y/N]` prompt, or a non-interactive `--install-uv` flag. It never installs uv silently.
3. Runs `uv sync --locked --all-groups`.
4. Resolves a config file: `--config PATH`, then the `FACE_PROFILE_CONFIG` environment variable, then the safe default `config/default.yaml`. Fails with a clear message if the resolved path does not exist.
5. Forwards any remaining arguments to `face-profile` unchanged; with no arguments it runs `check`.

```bash
./run.sh                                             # safe hardware-free check (default.yaml: everything disabled/mock)
./run.sh --help                                      # usage
./run.sh detect --debug-output /tmp/out.png           # any face-profile subcommand, still config-gated
./run.sh --config config/webcam-demo.yaml detect      # explicit opt-in to a local camera config (created by Webcam_demo.sh, or write your own — see below)
./run.sh serve                                        # only starts if api.enabled is explicitly true in the config
./run.sh --install-uv                                 # pre-approve bootstrapping uv non-interactively
```

`run.sh` never changes host settings or enables the camera, detector, database, recognition, enrollment, or API on its own — that only happens if the config file it is pointed at explicitly sets those fields, exactly as with the manual `uv run face-profile` invocation below. Use `Webcam_demo.sh` for a scripted, integrity-pinned local-webcam walkthrough, or hand `run.sh` your own `--config` for other local camera/API workflows.

## Continuous Launcher (`run-continuous.sh`)

`./run-continuous.sh` at the repository root is a separate launcher for the
long-running API daemon. `run.sh` itself is unchanged and remains the safe one-shot
`check` launcher; `run-continuous.sh` never duplicates its bootstrap, `uv`-install,
or process-exec logic — it locates the repository the same way (via its own script
path, independent of the caller's current directory) and then delegates to `run.sh`:

1. Resolves the repository directory the same way `run.sh` does, and refuses to run
   if it isn't next to this project's `pyproject.toml`.
2. Resolves a config file: `--config PATH`, then `FACE_PROFILE_CONFIG`, then the
   repository-shipped `config/continuous.yaml` (loopback-only API enabled; camera,
   detection, database, recognition, enrollment, and every other sensitive
   capability disabled/mock, exactly like `config/default.yaml`).
3. Always invokes `run.sh --config <resolved path> serve` (plus `--install-uv` if you
   passed it) — it never falls back to `run.sh`'s default `check`, and no
   `face-profile` subcommand other than `serve` can be requested through it. Any
   other argument is rejected before `uv`/`run.sh` is ever invoked.
4. Because this script, `run.sh`, and `run.sh`'s final `exec uv run ...` all use
   `exec`, signals (`Ctrl+C`/`SIGTERM`) and the process exit code reach the `uvicorn`
   process directly.

```bash
./run-continuous.sh                                  # serve config/continuous.yaml (loopback API only)
./run-continuous.sh --config data/my-private.yaml     # serve your own private config instead
./run-continuous.sh --install-uv                      # pre-approve bootstrapping uv non-interactively
./run-continuous.sh --help                            # usage
```

**This starts the REST/WebSocket API daemon only.** It does not enable camera
capture, face detection, the profile database, recognition, or enrollment — those
require your own private config that explicitly opts in (see "Explicit opt-ins" in
`docs/RUNNING_ON_UBUNTU.md` §6). `config/continuous.yaml` ships with no `auth_token`
because it never binds beyond loopback; do not edit that tracked file to add a
non-loopback `bind_host` or a token — use a private, gitignored config instead (see
`docs/RUNNING_ON_UBUNTU.md` §5).

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

## Configure One-Frame Detection

Detection remains disabled and mock-backed by default. A hardware-free contract check can enable the mock detector alongside the synthetic image source:

```yaml
detection:
  enabled: true
  backend: mock
  model_path: null
  model_sha256: null
  confidence_threshold: 0.5
  nms_threshold: 0.3
  top_k: 5000
```

Run one frame and optionally write a private debug PNG:

```bash
uv run face-profile --config /path/to/config.yaml detect --debug-output /tmp/face-profile-m2.png
```

The JSON result exposes only `face_count`; it does not log boxes, landmarks, pixels, or configured paths. Debug output is explicit, owner-only, and rejects symbolic-link following where supported.

For real local inference, set `backend: yunet`, an explicit `model_path`, and the exact lowercase SHA-256. The factory verifies the digest before OpenCV loads the file. M2 does not bundle a model or claim an accuracy evaluation. The interoperability smoke test used OpenCV Zoo's `face_detection_yunet_2023mar.onnx` (232,589 bytes), sourced from <https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx>, SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`. Its upstream model source records the BSD 3-Clause license; review current provenance and intended-use rights before deployment.

Expected startup failures are written as JSON to standard error:

- Exit `2`: missing or invalid configuration.
- Exit `3`: service resource startup or detection failure.
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

### Detector unavailable

Confirm `detection.enabled`, the backend-specific controls, model-file availability, and lowercase SHA-256. Model configuration is rejected when incomplete, and a missing, changed, or unloadable artifact fails with a controlled error that omits the configured path and backend details.

### Dependency drift

Run `uv lock --check`. If dependency changes are intentional, regenerate `uv.lock`, review the complete diff, and rerun all quality gates.

## Security Notes

- Never log frames, embeddings, candidate images, tokens, passwords, or arbitrary request payloads.
- Never commit `.env` files or runtime data.
- Use only synthetic, consented, or otherwise authorized media. Never commit real face fixtures.
- Treat future biometric exports and backups as sensitive encrypted material.
- Review `docs/THREAT_MODEL.md` before introducing a new external boundary.
