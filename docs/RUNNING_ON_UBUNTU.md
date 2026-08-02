# Running on Ubuntu

A practical, safety-first walkthrough for getting the Face Profile System running on an
Ubuntu machine. It complements `docs/RUNBOOK.md` (which documents the underlying
bootstrap/config/troubleshooting contract) with an end-to-end path: clone, install,
run the safe default, then opt in — one deliberate step at a time — to camera, model,
database, API, UI, and host-settings access.

**Read this first:** every milestone (M0–M15) in this repository has an implementation
*drafted* on `alpha`. Automated tests, real-hardware benchmarking, real-dataset accuracy
evaluation, and manual real-hardware verification are explicitly **deferred by the
owner** — see the per-milestone status in `docs/reports/`. Nothing in this guide should
be read as a claim that the system has been tested, hardware-validated, evaluated on a
dataset, or is production-ready. Every capability below is disabled by default and stays
disabled until you explicitly opt in.

## 1. Supported baseline

- Exact verified baseline: **CPython 3.11.15** and **uv 0.11.32** (pinned in
  `.python-version` and used by CI).
- CI also verifies **CPython 3.12.13** compatibility; `pyproject.toml` accepts any
  maintained 3.11/3.12 patch release (`requires-python = ">=3.11,<3.13"`).
- The committed `uv.lock` pins exact dependency versions so the toolchain is
  reproducible; always install with `--locked`.
- On an NVIDIA Jetson (JetPack Ubuntu, aarch64), everything in this guide still
  applies — see `docs/RUNNING_ON_JETSON.md` for the platform-specific addendum
  (Python toolchain reality, dependency wheel availability, CPU-only inference,
  `/dev/video*` permissions).

## 2. Clone the `alpha` branch

```bash
git clone -b alpha https://github.com/meanOtaku/Auto-Profiles.git
cd Auto-Profiles
```

If you already have a clone, switch and fast-forward instead:

```bash
git switch alpha
git pull --ff-only origin alpha
```

## 3. One-command safe path: `./run.sh`

```bash
./run.sh
```

This is the single-command launcher for a fresh Ubuntu machine (`run.sh`). With no
arguments it is **safe by default**:

1. It locates the repository from its own script path, regardless of your current
   directory, and refuses to run if it isn't sitting next to this project's
   `pyproject.toml`.
2. It requires `uv`. If `uv` is missing, it **never** installs it silently: it prints
   the official installer command
   (`curl -LsSf https://astral.sh/uv/install.sh | sh`, a user-space install to
   `~/.local/bin` — no `sudo`, no `apt`, no system configuration changes) and only runs
   it after you approve it — an interactive `[y/N]` prompt, or the `--install-uv` flag
   for non-interactive/scripted approval. It never uses `sudo`/`apt`.
3. It runs `uv sync --locked --all-groups`.
4. It resolves a config file in this order: `--config PATH`, then the
   `FACE_PROFILE_CONFIG` environment variable, then the safe default
   `config/default.yaml` (everything disabled/mock).
5. With no further arguments it runs the hardware-free `check` command. Any other
   arguments are forwarded to `face-profile` unchanged.

`run.sh` never touches a camera, loads a detector/embedding model, opens the database,
starts the API, or changes host audio/brightness settings unless the config file you
point it at explicitly turns those on.

```bash
./run.sh --help                                     # usage and options
./run.sh --install-uv                                # pre-approve bootstrapping uv non-interactively
./run.sh --config config/webcam-demo.yaml detect     # explicit opt-in to a local camera config (see §6)
./run.sh serve                                       # only starts if api.enabled is explicitly true
```

### Manual equivalent

```bash
uv sync --locked --all-groups
uv run face-profile --config config/default.yaml check
```

A successful `check` emits `ServiceStarted` and `ServiceStopped` JSON log lines and
exits `0`. It does not access a camera or change host settings.

### 3b. Continuous mode: `./run-continuous.sh`

`run.sh` above is deliberately one-shot and safe by default. If you want the
long-running API daemon instead, use the separate `./run-continuous.sh` launcher:

```bash
./run-continuous.sh
```

It locates the repository from its own script path the same way `run.sh` does
(independent of your current directory), and delegates all bootstrapping, `uv`
handling, and process execution to `run.sh` — it never duplicates that logic. It
always runs `serve` and never falls back to `check`; no other `face-profile`
subcommand can be forwarded through it, and any unrecognized argument is rejected
before `uv`/`run.sh` is ever invoked. With no arguments it serves the
repository-shipped `config/continuous.yaml`: the loopback-only REST/WebSocket API is
enabled (no `auth_token` required on loopback — see §7), while camera, detection,
database, recognition, enrollment, and every other sensitive capability stay
disabled/mock, exactly like `config/default.yaml`.

**This starts the API daemon only. It does not enable camera-based face
recognition** — that still requires your own private config that explicitly opts in
(§5–§6 below), passed via `--config`.

```bash
./run-continuous.sh --help                              # usage and options
./run-continuous.sh --config data/my-private.yaml         # serve your own private config instead
./run-continuous.sh --install-uv                          # pre-approve bootstrapping uv non-interactively
```

Stopping it is the same as stopping `serve` directly (§9): `Ctrl+C` for a graceful
shutdown, or `SIGTERM` if run under a process manager — this script, `run.sh`, and
`run.sh`'s final `exec uv run ...` all use `exec`, so the signal reaches the
`uvicorn` process directly.

## 4. The `face-profile` CLI surface

Every subcommand takes `--config PATH` (required) before the subcommand name:

```bash
uv run face-profile --config config/default.yaml <command> [options]
```

| Command | Purpose | Requires |
|---|---|---|
| `check` | Hardware-free service lifecycle check | nothing extra |
| `detect --debug-output PATH` | Run detection on one frame, optionally save an annotated debug PNG | `camera.enabled`, `detection.enabled` |
| `compare --image-a PATH --image-b PATH` | Full detect→quality→align→embed pipeline on two images, prints cosine similarity | `detection.enabled`, `quality.enabled`, `embedding.enabled` |
| `evaluate-threshold --pairs PATH [--thresholds a,b,c]` | Compute FAR/FRR/EER from a caller-supplied labeled-pair JSON dataset | a pairs file you provide — no bundled dataset |
| `profile create\|list\|show\|delete\|merge` | Manage permanent profiles | `database.enabled` |
| `recognize --image PATH [--track-id N]` | Run the pipeline and match against active profiles | `database.enabled`, `embedding.enabled` |
| `candidate list\|show\|approve\|reject` | Review temporary enrollment candidates | `database.enabled`, `enrollment.enabled` |
| `serve` | Start the FastAPI/uvicorn daemon | `api.enabled` |

Commands that require a disabled feature fail closed with a non-zero exit and a JSON
error on stderr (see §9) rather than silently doing nothing.

Examples once the relevant config sections are enabled (see §6):

```bash
uv run face-profile --config config/my-local.yaml detect --debug-output /tmp/out.png
uv run face-profile --config config/my-local.yaml profile create --display-name "Alice" --owner
uv run face-profile --config config/my-local.yaml profile list
uv run face-profile --config config/my-local.yaml candidate list --status ready_for_review
uv run face-profile --config config/my-local.yaml candidate approve --id <uuid> --reviewed-by alice
uv run face-profile --config config/my-local.yaml recognize --image /path/to/frame.png
```

`evaluate-threshold` and `compare` never need the database; they only exercise the
vision pipeline.

## 5. Use a private local config — don't edit the tracked defaults

`config/default.yaml`, `config/continuous.yaml`, and `config/jetson-webcam.yaml` are
the only config files tracked by git. `default.yaml` must stay fully disabled — it is
what `./run.sh` and CI both rely on as the safe baseline. `continuous.yaml` must stay
loopback-only with no `auth_token` — it is what `./run-continuous.sh` relies on as its
safe baseline; every other capability in it must stay disabled/mock like
`default.yaml`. `jetson-webcam.yaml` is the same loopback-only-API baseline with
`camera`/`detection`/`tracking` additionally enabled (USB webcam capture +
integrity-pinned YuNet + tracking only — see `docs/RUNNING_ON_JETSON.md` §5); it must
also stay loopback-only with no `auth_token`, and every capability beyond those three
sections must stay disabled/mock. **Don't edit any of these three tracked files** to
add database/recognition access, a non-loopback `bind_host`, or an `auth_token`.

Instead, copy it to a config that git does not track, and point `--config` /
`FACE_PROFILE_CONFIG` at that copy:

```bash
mkdir -p data          # data/ is already gitignored
cp config/default.yaml data/local.yaml
$EDITOR data/local.yaml
uv run face-profile --config data/local.yaml check
# or: export FACE_PROFILE_CONFIG=data/local.yaml
```

`data/` is excluded in `.gitignore`, so files placed there won't show up in `git
status` or get committed by accident. If you'd rather keep a config under `config/`
(as `Demo.sh`/`Webcam_demo.sh` do, writing `config/demo.yaml` / `config/webcam-demo.yaml`),
remember that directory is **not** gitignored beyond `default.yaml` — check `git
status` before running `git add`, and never commit a config containing an
`auth_token`, a real `camera.path`, or a non-default `database`/`api` setup. Storing
local configs outside the repository entirely (e.g. `~/.config/face-profile/local.yaml`)
avoids the question altogether.

## 6. Explicit opt-ins

Every section below is `enabled: false` (or an equivalent safe default) in
`config/default.yaml`. Nothing here activates unless you set it in your own private
config from §5.

### Camera

```yaml
camera:
  enabled: true
  source: webcam        # or: mock | image | video
  path: null             # required for image/video, must stay null for webcam
  device_index: 0
  retry_attempts: 2
```

`check` opens and closes the configured source without retaining a frame. `detect`
reads one frame. Webcam access needs host permission to the video device and no other
process holding it exclusively.

### YuNet face detector model

```yaml
detection:
  enabled: true
  backend: yunet          # mock is the disabled-by-default backend
  model_path: models/face_detection_yunet_2023mar.onnx
  model_sha256: 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
  confidence_threshold: 0.5
  nms_threshold: 0.3
  top_k: 5000
```

No model is bundled with the repository. `model_sha256` is mandatory once
`backend: yunet` is set: the loader verifies this exact digest against the file's
bytes before OpenCV ever loads it, and a mismatched, missing, or unconfigured digest
fails closed. The interoperability smoke test used OpenCV Zoo's
`face_detection_yunet_2023mar.onnx` (232,589 bytes) from
<https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx>
(BSD 3-Clause upstream license — review current provenance and intended-use rights
yourself before relying on it). `Webcam_demo.sh` automates downloading and
verifying this file into `models/` and writing a matching `config/webcam-demo.yaml`.

### Profile database (encryption key and data files)

```yaml
database:
  enabled: true
  path: data/face_profile.sqlite3
  key_path: data/face_profile.key
  retention_days: 30
  maximum_embeddings_per_profile: 50
```

- The database directory and key-file directory are created with `0700` permissions;
  the SQLite file and the key file are hardened to `0600`. The key is stored
  **separately** from the database file, in its own file, by design (ADR 0011) — do
  not point `key_path` inside a directory you back up or sync alongside the database.
- If the key file exists but its permissions are looser than owner-only, loading
  fails closed (`KeyUnavailableError`) rather than silently trusting it.
- `data/` is gitignored — never commit the database file, the key file, or any
  export/import produced from them.
- `recognition`, `enrollment`, and `candidate`/`profile`/`recognize` CLI commands all
  require `database.enabled: true` and fail closed with `database_disabled` otherwise.

### Recognition, quality, and embedding (needed for `compare`/`recognize`)

`recognition.enabled` additionally requires `embedding.enabled`. `quality.enabled` and
an embedding backend (`mock` for a model-free deterministic check, or an
integrity-pinned ONNX model via `embedding.model_path`/`embedding.model_sha256` for a
real one) gate `compare`/`recognize`/candidate enrollment. No embedding model artifact
is bundled and no accuracy claim is made for any threshold — see `docs/reports/M5.md`
and `docs/reports/M7.md` before relying on `similarity_threshold`/`similarity_margin`.

### Candidate enrollment

```yaml
enrollment:
  enabled: true
  automatic_promotion: false
  automatic_promotion_consent_acknowledged: false
```

Manual approval remains the safe default. Setting `automatic_promotion: true`
is accepted only when `automatic_promotion_consent_acknowledged: true`, liveness,
the full camera/quality/embedding/recognition/enrollment pipeline, encrypted
database storage, finite retention, and the API are enabled. A non-loopback API
still requires authentication. The acknowledgement asserts operator authority;
it does not collect consent from people seen by the camera. Passive liveness is
unevaluated and is not production-grade anti-spoofing. See the explicit high-risk
example in `config/jetson-full.yaml`.

### API / headless daemon

```yaml
api:
  enabled: true
  bind_host: 127.0.0.1     # loopback by default
  bind_port: 8443
  auth_token: null          # required if bind_host is non-loopback
  rate_limit_per_minute: 120
  max_request_body_bytes: 1000000
```

See §7 for the auth/loopback rules in detail. `serve` only starts if `api.enabled` is
`true`; otherwise it fails closed with `api_disabled`.

### Optional static UI dashboard

```yaml
ui:
  enabled: true
```

The dashboard is a static HTML page (`src/face_profile/ui/dashboard.html`) served at
`GET /` only when `ui.enabled` is true and `serve` is running. It is a pure
`/api/v1` REST client with no server-side logic of its own — you paste the configured
`auth_token` into the page (kept only in page memory, never persisted) if one is
configured. It has only been checked via `TestClient` for HTML delivery, not
exercised in a real browser against a live server in this repository (see
`docs/reports/M13.md`).

### Liveness

```yaml
liveness:
  enabled: true
  require_active_challenge: false
```

The passive checks are classical spectral/reflectance heuristics, **not a trained
anti-spoof model**, and have no evaluated real-world accuracy (`docs/reports/M14.md`,
ADR 0019). No printed-photo or spoof test set exists in this repository; no
spoof-detection accuracy rate is claimed.

### Linux host settings (volume/brightness)

```yaml
settings:
  backend: linux    # mock is the default; this is the one real adapter implemented
  volume: 50
  brightness: 50
  min_apply_interval_seconds: 0.2
```

`linux` uses ALSA `amixer` for volume and `/sys/class/backlight/*` for brightness, and
requires those to actually exist and be writable on your machine — otherwise the
adapter reports the corresponding capability as unsupported/permission-denied rather
than mutating anything. The HERMES-required manual real-hardware verification of this
adapter has **not** been performed by the owner (`docs/reports/M9.md`); treat it as
implemented-but-unverified on real hardware.

## 7. API bearer-token and non-loopback safety

- `bind_host` defaults to loopback (`127.0.0.1`, `::1`, or `localhost`).
- If you set `bind_host` to anything else (e.g. `0.0.0.0` or a LAN address) **without**
  an `auth_token`, config loading itself fails — this is enforced at config-validation
  time, not just at request time (`APIConfig.validate_fail_closed`, ADR 0017).
- `auth_token`, once set, must be 16–256 characters and is required on every
  authenticated request as `Authorization: Bearer <token>` (checked with a
  constant-time comparison), including on loopback if you opt into a token there too.
  `GET /api/v1/health` is the only unauthenticated endpoint.
- The WebSocket endpoint (`/api/v1/events/live`) enforces the same token via its
  handshake.
- A body-size limit (`max_request_body_bytes`) and a fixed-window
  `rate_limit_per_minute` (per client host) apply to all requests except `/health`.
- Never put the `auth_token` in a config file you commit (see §5) or in application
  logs — logging never includes tokens, passwords, or request payloads by design.

Start the server only after reviewing this section and your config:

```bash
uv run face-profile --config data/local.yaml serve
# or: ./run.sh --config data/local.yaml serve
```

## 8. Runtime data and key safety checklist

- `data/` is gitignored — keep the SQLite database, the encryption key, debug PNGs,
  and any export/import files there or otherwise outside version control.
- Never commit real face images, embeddings, credentials, profile databases, model
  artifacts without provenance review, or runtime exports (`README.md` "Safety and
  Privacy"; `docs/THREAT_MODEL.md`).
- Debug output (`detect --debug-output PATH`) is only written when you pass an
  explicit path; it is not written by default and the path is owner-only and rejects
  symlink-following where the OS supports it.
- Treat any future biometric export/backup as sensitive encrypted material.

## 9. Stop and troubleshoot

- **Stopping `serve`**: it's a foreground `uvicorn` process — `Ctrl+C` for a graceful
  shutdown (or send `SIGTERM` if run in the background/under a process manager).
- **Stopping any other command**: they are one-shot; they exit on their own.
- **Exit codes** (from `check`/`detect`/`compare`/`recognize`/etc.): `0` success,
  `2` invalid/rejected configuration, `3` resource startup or command failure
  (e.g. `detector_unavailable`, `database_disabled`, `api_disabled`,
  `recognition_disabled`), `4` shutdown/cleanup failure. Errors are structured JSON on
  stderr with an `error_code` field — never a stack trace with sensitive data.
- **Configuration rejected**: check YAML syntax, remove unknown keys (the schema is
  strict and rejects them), and use native YAML booleans/integers. Never put
  credentials in the file itself if you're going to commit it — see §5/§7.
- **Camera unavailable**: for `image`/`video`, confirm `path` exists and is a
  supported file. For `webcam`, confirm `device_index`, host permissions, and that no
  other process holds the device exclusively. Startup fails closed with exit `3`,
  and the raised error message now includes a best-effort actionable hint (missing
  `/dev/videoN`, permission gap, or device busy — see `docs/RUNNING_ON_JETSON.md` §3
  and §7 for the full explanation and fixes, which apply on any Linux host, not just
  Jetson).
- **Detector unavailable**: confirm `detection.enabled`, `model_path` points at a real
  file, and `model_sha256` is the exact lowercase digest of that file's current bytes.
- **`uv sync` / dependency drift**: run `uv lock --check`; if a dependency change is
  intentional, regenerate `uv.lock`, review the diff, and rerun quality gates.
- **`run.sh` can't find the repo**: it locates itself via its own script path and
  requires `pyproject.toml` (`name = "face-profile-system"`) next to it — don't copy
  `run.sh` out of the repository root.
- For the full quality-gate command set (`ruff`, `mypy`, `pytest`, `pip-audit`,
  `pip-licenses`, `uv build`), see `README.md` and `docs/RUNBOOK.md`.

## 10. What's drafted vs. not verified

This is a summary pointer, not a substitute for the authoritative status: read the
per-milestone report under `docs/reports/M*.md` and the top of `README.md` before
treating any capability as validated.

- Every milestone M0–M15 has an **implementation drafted** on `alpha`.
- Automated test suites, real-hardware benchmarking/latency numbers, real-dataset
  biometric accuracy evaluation (FAR/FRR/EER on an actual labeled dataset), the
  HERMES-required manual real-hardware settings-adapter test, and full
  production-readiness sign-off are all **explicitly deferred by the owner** — none of
  that has been claimed as done in this repository, and this guide does not claim it
  either.
- Where a milestone's own smoke testing found and fixed a real bug (e.g. a
  cross-thread SQLite crash in M12, a merge-time decryption bug in M6), that is
  recorded in the relevant `docs/reports/M*.md`, not implied to mean the milestone as
  a whole is verified.
- Liveness accuracy, spoof-resistance accuracy, and detector/embedding model accuracy
  are all explicitly **not evaluated** in this repository — no numbers are claimed for
  any of them.

If you perform your own verification (tests, hardware validation, dataset evaluation),
record it in the relevant `docs/reports/M*.md` rather than assuming this guide reflects
it.
