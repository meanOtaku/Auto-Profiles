# Running on an NVIDIA Jetson (JetPack Ubuntu)

A practical setup/run/diagnostics guide for getting the Face Profile System installed
and running on an NVIDIA Jetson (Orin, Xavier, Nano) running a JetPack Ubuntu image,
with a standard USB UVC/V4L2 webcam, in a likely **headless** (no desktop session)
environment. It assumes you've already read `docs/RUNNING_ON_UBUNTU.md` — this guide
only covers what is genuinely different on Jetson/aarch64: Python toolchain reality,
dependency-wheel availability, the CPU-vs-GPU inference tradeoff, and `/dev/video*`
permissions. Everything else (config opt-ins, `run.sh`, CLI surface, exit codes, safety
rules) is identical to the Ubuntu guide and is not repeated here.

**No physical Jetson hardware, camera, or GPU was available in the environment that
produced this guide and the changes it documents.** Every command and dependency claim
below was verified the ways that are possible without that hardware — reading the
locked dependency manifest (`uv.lock`) for aarch64 wheel availability, reading the
source for GPU/CUDA/display assumptions, and running the automated test suite — not by
executing them on a Jetson. Section 8 lists exactly what still needs a real device.

## 1. The three JetPack realities that matter here

### 1.1 Python version: JetPack's system Python is too old, and that's fine

`pyproject.toml` requires `>=3.11,<3.13`. JetPack's apt-provided `python3` is tied to
its Ubuntu base and is typically **older** than that (Python 3.8 on JetPack 5 /
Ubuntu 20.04, Python 3.10 on JetPack 6 / Ubuntu 22.04) — neither satisfies the
constraint, and you should not try to force the system interpreter to work.

You don't need to build Python from source or fight `apt` for this. `uv` (which this
project already requires — see `run.sh`) manages its own interpreters: `uv sync`
downloads a standalone CPython build matching `.python-version` (currently
`3.11.15`) for `aarch64-unknown-linux-gnu` and uses that, independent of whatever
`python3` resolves to on the system. Do not `apt install python3.11` or add a
deadsnakes-style PPA — it isn't necessary and is one more thing to keep patched.

### 1.2 Dependency wheels: no compilation needed, but check glibc on old images

Every runtime dependency this project pins already ships a `manylinux`/`abi3` wheel
for `aarch64`, so `uv sync --locked` on a Jetson does **not** need to compile OpenCV,
cryptography, or anything else from source — the multi-hour "build OpenCV on a
Jetson" story you may have seen elsewhere does not apply to this project's pinned
dependency, `opencv-python-headless==5.0.0.93` (see `uv.lock`: it publishes
`manylinux2014_aarch64` and `manylinux_2_28_aarch64` wheels under a `cp37-abi3` tag,
so it covers 3.11/3.12 without a per-version rebuild).

The one thing worth checking on an **older** JetPack base image (JetPack 4 /
Ubuntu 18.04, glibc 2.27) is that pip/uv correctly falls back to the
`manylinux2014` (glibc ≥ 2.17) wheel rather than the newer `manylinux_2_28` one —
this is automatic (wheel tag negotiation), not something you configure, but if
`uv sync --locked` ever reports it cannot find a compatible wheel for
`opencv-python-headless`, that is the first thing to check (`ldd --version`).
JetPack 5 (Ubuntu 20.04, glibc 2.31) and JetPack 6 (Ubuntu 22.04, glibc 2.35) are
both comfortably new enough for either tag.

```bash
uv sync --locked --all-groups   # same command as docs/RUNNING_ON_UBUNTU.md §3
```

### 1.3 CUDA / TensorRT: this project does not use them, by design, on any platform

JetPack's headline feature is GPU-accelerated OpenCV/CUDA/TensorRT via `apt`
(`nvidia-l4t-*` packages, `libopencv-*` built with CUDA). **This project does not use
that OpenCV build and does not use TensorRT.** It depends on the PyPI
`opencv-python-headless` wheel (§1.2), which is a CPU-only build — no CUDA, no
cuDNN, no GStreamer/nvargus. All inference in this codebase goes through
`cv2.dnn.readNetFromONNX` / `cv2.FaceDetectorYN.create` (see
`src/face_profile/vision/factory.py` and `src/face_profile/vision/embedding.py`),
which on this wheel runs on the Jetson's ARM CPU cores, not its GPU or DLA.

This is a deliberate reproducibility/portability tradeoff already baked into
`pyproject.toml`, not a Jetson-specific gap introduced by this guide, and there is no
code path in this repository that assumes CUDA or TensorRT are present. The
consequence is purely a performance one: expect detector/embedding inference to be
slower on a Jetson's CPU than a desktop-class x86 CPU, and it will **not** use the
Jetson's GPU/DLA at all. No FPS/latency number is claimed anywhere in this
repository for any platform (`docs/reports/M15.md`) — if you need GPU-accelerated
inference on Jetson, that would require a separate TensorRT/CUDA execution provider
integration, which is out of scope for the current milestone set and is not present
here.

## 2. Install

```bash
git clone -b alpha https://github.com/meanOtaku/Auto-Profiles.git
cd Auto-Profiles
./run.sh          # safe hardware-free check; installs uv (with your approval) and
                   # runs `uv sync --locked --all-groups`, same as on x86_64 Ubuntu
```

See `docs/RUNNING_ON_UBUNTU.md` §3 for exactly what `run.sh` does and does not do
(no `sudo`, no `apt`, no camera/model/database/API access without an explicit
`--config`). Nothing about that behavior differs on Jetson.

## 3. `/dev/video*` permissions for a USB UVC webcam

A standard USB webcam on a Jetson enumerates as a normal V4L2 device
(`/dev/video0`, `/dev/video1`, ...) exactly like on any other Linux box — no CSI
camera driver, no `nvarguscamerasrc`/GStreamer pipeline, and no Jetson-specific
kernel module is involved for a UVC device. What differs from a typical desktop
Ubuntu install is that a fresh JetPack flash's default user is often **not** yet in
the `video` group, and JetPack images are frequently provisioned headless (SSH-only,
no desktop session, no logged-in user in the udev "seat" that would otherwise grant
device access).

```bash
ls -l /dev/video*                 # confirm the device node exists and note its group
groups                            # confirm your user is in 'video' (JetPack default: often not)
sudo usermod -aG video "$USER"    # add yourself to the group if missing
# then start a new login session (log out/in, new SSH session, or reboot) —
# group membership does not apply to your already-open shell
```

This project's own `WebcamFrameSource` (`src/face_profile/camera/__init__.py`) now
surfaces this directly: when `cv2.VideoCapture` fails to open a configured
`device_index`, it runs a best-effort, hardware-free check
(`src/face_profile/camera/diagnostics.py`) and folds the result into the raised
error message — telling you whether `/dev/videoN` doesn't exist at all (wrong
`device_index`, or the camera isn't enumerated — check `v4l2-ctl --list-devices`
and `dmesg | tail`), exists but isn't readable/writable by this process (the `video`
group case above), or exists and is accessible but still wouldn't open (likely held
by another process — check `fuser /dev/videoN` / `lsof /dev/videoN`). The `check`/
`detect` CLI commands surface this in the structured JSON error on stderr (see
`docs/RUNNING_ON_UBUNTU.md` §9) — read the log line's message field, not just its
`error_code`, for the actionable detail.

On Linux (Jetson included), `WebcamFrameSource` now also opens the device with V4L2
explicitly requested (`cv2.CAP_V4L2`) rather than relying on OpenCV's default backend
auto-detection, so a USB UVC camera is addressed deterministically as a V4L2 device
regardless of whatever other video I/O backends happen to be compiled into the
`opencv-python-headless` wheel.

Useful verification tools (install via `sudo apt install v4l-utils` if missing —
this is a standard Ubuntu package, not JetPack-specific):

```bash
v4l2-ctl --list-devices                  # enumerate cameras and their /dev/videoN indices
v4l2-ctl --device=/dev/video0 --all      # confirm the driver, supported formats, current settings
```

## 4. Select the right `device_index` and run a headless check

Camera access stays disabled until you opt in from your own private config (see
`docs/RUNNING_ON_UBUNTU.md` §5–§6 — never edit `config/default.yaml`). A USB webcam
frequently registers two `/dev/videoN` nodes (one for actual video capture, one for
metadata); `v4l2-ctl --list-devices` groups them per physical device so you can tell
which index is the capture node.

```yaml
camera:
  enabled: true
  source: webcam
  path: null
  device_index: 0        # match whatever v4l2-ctl --list-devices showed
  retry_attempts: 2
```

```bash
uv run face-profile --config data/local.yaml check          # hardware-free lifecycle check
uv run face-profile --config data/local.yaml detect --debug-output /tmp/out.png
```

Both commands are plain CLI processes — no display, window manager, or X/Wayland
session is required or touched. This codebase has no `cv2.imshow`/`cv2.waitKey`/
GUI calls anywhere (enforced by
`tests/unit/test_headless_safety.py::test_source_tree_never_calls_opencv_gui_functions`),
which is exactly why the project depends on `opencv-python-headless` rather than
`opencv-python` — the headless wheel has no GUI backend compiled in at all, so this
also protects you from a future change accidentally adding a call that would crash
on a headless Jetson.

## 5. Headless-specific notes

- **No desktop session needed.** SSH in, run the CLI, done — see §4.
- **`serve` (the REST/WebSocket API)** binds to loopback (`127.0.0.1`) by default; if
  you want to reach it from another machine on the network, you must set a
  non-default `bind_host` **and** an `auth_token` — config load fails closed
  otherwise (`docs/RUNNING_ON_UBUNTU.md` §7). This is unrelated to Jetson
  specifically but is the mechanism you'd use to reach a headless Jetson's API
  from a laptop on the same LAN.
- **`settings.backend: linux`** (ALSA `amixer` volume, sysfs backlight) assumes the
  corresponding hardware/interfaces exist; a headless Jetson with no display
  attached typically has no backlight device, and a minimal image may have no ALSA
  mixer configured. The adapter reports the capability as unsupported rather than
  failing the whole service (`docs/reports/M9.md`) — this is pre-existing behavior,
  not something this guide changes.
- **No systemd unit is provided by this repository.** If you want the service to
  survive an SSH disconnect or auto-start on boot, wrap `uv run face-profile
  --config ... serve` (or another subcommand) in your own systemd service or
  equivalent — this is standard Linux process-supervision setup, not specific to
  this project.
- **`./run-continuous.sh`** is a separate launcher, alongside `run.sh`, that always
  starts `serve` (never the one-shot `check`) through `run.sh`/`uv` — nothing about
  it differs on Jetson (`docs/RUNNING_ON_UBUNTU.md` §3b). By default it serves the
  repository-shipped `config/continuous.yaml`, which enables only the loopback API
  and keeps the camera/detector/database disabled — it does **not** enable webcam
  recognition on its own. To run `serve` against your own webcam-backed private
  config from §3–§4 above instead, pass it explicitly:
  `./run-continuous.sh --config data/local.yaml`.

## 6. Verification commands (run these — they don't need a camera)

```bash
uv sync --locked --all-groups                             # confirm aarch64 wheels resolve, §1.2
uv run face-profile --config config/default.yaml check     # hardware-free lifecycle check, §2
uv run pytest tests/unit/test_camera_diagnostics.py \
              tests/unit/test_frame_sources.py \
              tests/unit/test_headless_safety.py -q        # camera/platform/headless-safety tests
uv run pytest                                               # full suite (hardware-free, see docs/RUNNING_ON_UBUNTU.md)
uv run mypy src tests
uv run ruff check .
```

And these, once a camera is attached (§3–§4):

```bash
v4l2-ctl --list-devices
uv run face-profile --config data/local.yaml check
uv run face-profile --config data/local.yaml detect --debug-output /tmp/out.png
```

## 7. Troubleshooting quick-reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `uv sync --locked` fails to resolve `opencv-python-headless` | Glibc too old (pre-JetPack-5-class image) or `--locked` drift | `ldd --version`; confirm ≥ glibc 2.17. If genuinely intentional dependency drift, see `docs/RUNNING_ON_UBUNTU.md` §9 on `uv lock --check` |
| `unable to open webcam source: N (/dev/videoN does not exist...)` | Wrong `device_index`, camera not enumerated | `v4l2-ctl --list-devices`, `dmesg \| tail`, re-check cabling/power |
| `unable to open webcam source: N (/dev/videoN exists but is not readable/writable...)` | User not in `video` group | §3: `sudo usermod -aG video "$USER"`, then start a **new** login session |
| `unable to open webcam source: N (/dev/videoN exists and is accessible, but OpenCV could not open it...)` | Device held by another process, or driver needs a moment after plugin | `fuser /dev/videoN` / `lsof /dev/videoN`; retry after a few seconds |
| Detection/embedding is much slower than expected | Reading §1.3 as a CUDA/TensorRT bug | It's expected: this project runs CPU-only inference on every platform, including Jetson — no GPU/DLA is used |
| `settings.backend: linux` reports volume/brightness unsupported | No ALSA mixer or backlight device on this headless image | Expected on a minimal/headless image; not a bug (`docs/reports/M9.md`) |

## 8. What this guide does not and cannot claim

Consistent with `docs/RUNNING_ON_UBUNTU.md` §10 and every `docs/reports/M*.md`: no
milestone in this repository has been verified against real hardware by the owner,
and this guide does not change that. Specifically **not** performed in the
environment that produced this guide (no Jetson device, no GPU, no physical camera
were available):

- Installing or running anything on an actual Jetson device or JetPack image.
- Opening a real `/dev/video*` UVC webcam, in any state (present, permission-denied,
  busy, unplugged mid-run).
- Any FPS/latency measurement, CPU-only or otherwise, on Jetson or any other ARM
  hardware.
- Verifying the `v4l2-ctl` command output or flag names against a real V4L2 driver
  on this hardware family beyond what is documented in `v4l-utils` upstream.
- Confirming `uv`'s standalone-Python download actually succeeds for
  `aarch64-unknown-linux-gnu` from this network/environment (§1.1 describes `uv`'s
  documented behavior; it was not exercised here).

What **was** done to produce the claims above: reading `uv.lock` for the exact
aarch64 wheel tags this project's pinned dependencies publish (§1.2), reading the
full source tree for CUDA/TensorRT/GUI assumptions (§1.3, §4 — confirmed there are
none), and adding/running the automated tests in
`tests/unit/test_camera_diagnostics.py`, the new cases in
`tests/unit/test_frame_sources.py`, and `tests/unit/test_headless_safety.py`, all of
which pass in this (x86_64, non-Jetson) environment using dependency-injected fakes
rather than real hardware — per this project's own rule (`CODING_STANDARDS.md` §21:
"Use real cameras in unit tests" is a prohibited pattern). If you run this on real
Jetson hardware, please record what you found in a new or existing
`docs/reports/M*.md` rather than assuming this guide reflects it.
