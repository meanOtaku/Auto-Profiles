# Running on Windows (10/11, x64)

A practical setup/run/diagnostics guide for getting the Face Profile System installed
and running from source on native Windows 10 or 11, x64, with a built-in or USB
webcam, in PowerShell -- no WSL, no Git Bash, no admin-elevated prompt required for
the normal path. It assumes you've already skimmed `docs/RUNNING_ON_UBUNTU.md` --
this guide covers what is genuinely different on Windows (PowerShell launchers,
DirectShow camera capture, pycaw/Core Audio volume, WMI brightness, NTFS DACLs
instead of POSIX mode bits, and Windows Firewall). Everything else (config opt-ins,
CLI subcommands, exit codes, safety rules) is identical to the Ubuntu guide and is
not repeated here.

**No Windows machine, camera, audio endpoint, or brightness-capable display was
available in the environment that produced this guide and the M17 changes it
documents.** Every command and code claim below was verified the ways that are
possible without that hardware -- reading the source (`settings/windows.py`,
`_win_security.py`, `platform_security.py`, `camera/__init__.py`,
`camera/diagnostics.py`, `diagnostics/preflight.py`), running Ruff/mypy against it,
and exercising the cross-platform parts of the CLI/config loader for real in this
(Linux) sandbox -- not by executing anything on a real Windows host. Section 9 below
and `docs/reports/M17.md` list exactly what still needs a real Windows machine, and
the `windows-compatibility` GitHub Actions workflow (`workflow_dispatch`-only, not
part of the required push/PR gate) is the first piece of *real* Windows-host
evidence this project will have once it is actually run -- it has not been run yet
as of this guide's writing.

## 1. Prerequisites

- **Windows 10 or 11, x64.** ARM64 Windows is not a verified target (no ARM64 wheel
  availability check has been performed for this project's pinned dependencies).
- **PowerShell.** The launchers (`run.ps1`, `run-continuous.ps1`,
  `scripts\provision-models.ps1`) are plain `.ps1` scripts written to run under
  either Windows PowerShell 5.1 (built into Windows 10/11, no install needed) or
  PowerShell 7+ (`pwsh`, [https://github.com/PowerShell/PowerShell]) -- they
  avoid PowerShell-7-only syntax (no `??`, no ternary `?:`) so either works. No
  admin/elevated PowerShell session is required to run them.
- **uv.** Same role as on Linux: manages an isolated Python 3.11/3.12 interpreter
  and the locked dependency set, independent of whatever `python` resolves to on
  PATH. `run.ps1` checks for it and, if missing, prints the official installer
  command (`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`)
  and exits. The launcher never downloads or executes installer code itself.
- **A webcam** (built-in or USB UVC) if you intend to use `config/windows-full.yaml`
  -- not required for `config/windows-safe.yaml`.
- Git, to clone the repository.

## 2. Install

```powershell
git clone -b alpha https://github.com/meanOtaku/Auto-Profiles.git
cd Auto-Profiles
.\run.ps1 -Config config\windows-safe.yaml check   # safe, hardware-free check first
```

`.\run.ps1` resolves the repository root from its own script location (works
regardless of your current directory), requires an already-installed `uv`, and runs
`uv sync --locked --all-groups`. See §3 for why the *first* run should pass
`-Config config\windows-safe.yaml` explicitly even though it is not `run.ps1`'s
own default.

If PowerShell refuses to run the script at all (`... cannot be loaded because
running scripts is disabled on this system`), that is Windows' default script
execution policy, not a bug in this project. Check your current policy and, if
you choose to change it, prefer the least-privileged option:

```powershell
Get-ExecutionPolicy -List
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

`RemoteSigned` (current-user scope) allows locally-authored scripts like this
repository's `.ps1` files to run while still requiring a signature for scripts
downloaded from the internet. This project does not instruct you to disable
script signature checks entirely (`-ExecutionPolicy Bypass` system-wide) and you
should not need to.

## 3. Understand the two safe validation paths

Unlike `run.sh`, **`run.ps1` with no arguments selects
`config/windows-full.yaml` but runs the non-mutating `preflight` command**. It
verifies both pinned model files and probes pycaw/Core Audio plus WMI brightness
capabilities. Windows preflight deliberately does not open the webcam because
Windows has no stable `/dev/videoN`-style path to inspect without opening it.
The launcher warns that later `serve`/`detect` commands use the real camera and
settings enabled by the full config.

For a completely hardware-free, non-mutating check -- including no pycaw or WMI
probe -- pass:

```powershell
.\run.ps1 -Config config\windows-safe.yaml check
```

`config/windows-safe.yaml` is byte-for-byte equivalent in effect to
`config/default.yaml`: camera, detection, embedding, database, recognition,
enrollment, API, UI, and liveness are all disabled or mock-backed, and
`settings.backend` stays `mock` (never `windows`) -- it never calls into pycaw or
WMI. This is also the config the `windows-compatibility` CI workflow uses (§8).

## 4. Provision the pinned models

```powershell
.\scripts\provision-models.ps1              # downloads into .\models\, idempotent,
                                              # fails closed on any hash mismatch
```

Downloads the same two OpenCV Zoo models as `scripts/provision-models.sh` (YuNet
detector, SFace embedder) from the same URLs, to the same pinned SHA-256 values --
see `docs/MODELS.md` for full provenance. Verification uses `Get-FileHash
-Algorithm SHA256`, compared case-insensitively; a downloaded file is written to a
temporary file in the destination directory first and only renamed into place
after the hash matches, so a partial or corrupted download can never become the
file OpenCV loads. Unlike the bash script (whose destination is relative to your
current directory), `-DestDir` here is resolved relative to the repository root
regardless of where you run it from.

```powershell
.\scripts\provision-models.ps1 -DestDir data\models   # custom, still repo-root-relative
```

## 5. Safe check, then the full path

```powershell
.\run.ps1 -Config config\windows-safe.yaml check         # hardware-free, non-mutating
.\run.ps1 -Config config\windows-full.yaml preflight      # model hashes + settings capabilities; camera open deferred
.\run.ps1                                                 # equivalent to the preflight command above
.\run.ps1 serve                                           # starts the full camera/API pipeline
```

`.\run.ps1 serve` starts the real pipeline in the foreground: webcam capture via DirectShow →
integrity-pinned YuNet detection → tracking → quality/alignment →
integrity-pinned SFace embedding → recognition → active-user selection →
load-and-apply that profile's saved settings → observe/debounce/save real
changes back, exposed only through the loopback-only REST/WebSocket API and
dashboard (`http://127.0.0.1:8443/`). See `config/windows-full.yaml`'s own header
comment for exactly what it enables, what it deliberately does not (LAN
exposure, automatic candidate promotion), and why it differs from
`config/jetson-full.yaml` on automatic enrollment.

```powershell
.\run.ps1 detect --debug-output out.png     # opens the configured camera path
.\run.ps1 serve                             # only starts if api.enabled is true in the config
```

## 6. Continuous mode: `run-continuous.ps1`

```powershell
.\run-continuous.ps1                                    # serve config/continuous.yaml (loopback API only, safe default)
.\run-continuous.ps1 -Config config\windows-full.yaml    # serve the real camera/model/settings config instead
```

`run-continuous.ps1`'s own default is `config/continuous.yaml` -- **not**
`windows-full.yaml` -- deliberately keeping the safe default for an unattended,
auto-restarting loop even though `run.ps1` itself defaults to the full config;
see `run-continuous.ps1`'s own header comment for the reasoning.

Windows has no systemd. This script is a self-contained **foreground restart
loop**: if `face-profile serve` exits with a non-zero code (a crash), it restarts
after a bounded delay that grows exponentially (1s, 2s, 4s, ... capped at 60s) and
resets back to 1s once a session has stayed up for at least 30 seconds; a clean
exit (code 0) is not restarted. **This is not a real Windows Service** -- there is
no `New-Service`/`sc.exe`/NSSM-style integration, no registration to auto-start on
boot, and no guarantee that it survives closing the terminal, logging off, or a
reboot. If you want that, wrap `.\run.ps1 -Config ... serve` in your own Windows
Service (e.g. via NSSM or a Scheduled Task configured to run at logon), a Windows
equivalent of `docs/RUNBOOK.md`'s "wrap it in your own systemd service" guidance
for Linux -- this repository does not provide or claim one.

**Ctrl+C** is not intercepted or trapped by this script: Windows delivers it to
the whole console process group (`run-continuous.ps1`, `run.ps1`, and the child
`uv run face-profile` process together), so pressing it terminates everything
immediately and the loop never treats that as a crash to restart from.

## 7. Camera: privacy settings, drivers, and device index

If `cv2.VideoCapture(device_index, cv2.CAP_DSHOW)` fails to open (surfaced in the
CLI's structured JSON error, and in `diagnose_webcam()`'s hint text --
`src/face_profile/camera/diagnostics.py`), check, in order:

1. **Windows camera privacy settings.** Settings → Privacy & security → Camera:
   both "Camera access" (the device-wide toggle) and "Let apps access your
   camera" must be on, **and** "Let desktop apps access your camera" must also be
   on -- this project is a console/desktop app, not a Microsoft-Store app, so it
   is gated by that specific toggle, which is easy to miss since it is listed
   separately from the per-app UWP toggles above it.
2. **Device Manager** → Cameras (or Imaging devices, on some driver stacks): a
   yellow-triangle warning means a driver problem outside this project's control
   -- reinstall or update the driver through Device Manager or Windows Update.
3. **Exclusive access.** Windows' camera driver model generally allows only one
   exclusive-mode consumer at a time (unlike Linux V4L2's more permissive
   sharing) -- close the Camera app, any video-call client, or another running
   instance of this service before retrying.
4. **`device_index` instability.** Windows does not expose stable,
   `/dev/videoN`-style enumerable device paths the way Linux does -- OpenCV's
   DirectShow backend assigns indices in enumeration order, which can change
   across reboots or when USB hubs/docks are attached or detached. There is no
   reliable stat/probe this project can perform ahead of time to confirm which
   index maps to which physical camera (`diagnostics/preflight.py`'s camera
   check is explicitly deferred to runtime capture on Windows, not silently
   skipped or falsely claimed as verified -- see that module's docstring). If
   the wrong camera opens, or none does, try adjacent small integers
   (`device_index: 0`, `1`, `2`, ...) in your config.

This project intentionally does not attempt to enumerate Windows cameras itself
(e.g. via `Get-PnpDevice`/WMI) and correlate that list with OpenCV's DirectShow
index order -- the two enumeration orders are not guaranteed to match, and
publishing an unverified mapping would be worse than no mapping at all.

## 8. Verification commands (run these -- they don't need a camera)

```powershell
uv sync --locked --all-groups
.\run.ps1 -Config config\windows-safe.yaml check
uv run ruff check .
uv run ruff format --check src/face_profile/_win_security.py src/face_profile/platform_security.py src/face_profile/settings/windows.py
uv run mypy src
uv build
```

And these, once a camera/audio endpoint/display is available:

```powershell
.\run.ps1 -Config config\windows-full.yaml preflight
.\run.ps1 -Config config\windows-full.yaml detect --debug-output out.png
```

The `.github/workflows/windows-compatibility.yml` GitHub Actions workflow
(`workflow_dispatch`-only, `windows-latest`, Python 3.11/3.12 matrix) runs the
first block above on a real Windows GitHub-hosted runner: locked dependency sync,
`compileall`, Ruff, strict mypy, `uv build`, installing the built wheel into a
clean `uv` environment, and the hardware-disabled `check` against
`config/windows-safe.yaml`. It never runs `pytest` or any test runner, and it is
not part of the required push/PR gate (`ci.yml`) -- it must be triggered manually,
and as of this guide's writing it has not yet been run for real (see
`docs/reports/M17.md`).

## 9. What this guide does not and cannot claim

Consistent with `docs/RUNNING_ON_UBUNTU.md` §10, `docs/RUNNING_ON_JETSON.md` §8,
and every `docs/reports/M*.md`: no milestone in this repository has been verified
against real target hardware by the owner, and this guide does not change that.
Specifically **not** performed in the environment that produced this guide and
the M17 changes (no Windows machine, camera, audio endpoint, or brightness-
capable display were available):

- Installing or running anything on an actual Windows 10 or 11 machine, in any
  PowerShell version.
- Opening a real webcam through `cv2.VideoCapture(..., cv2.CAP_DSHOW)`, in any
  state (present, privacy-blocked, driver-missing, exclusively held).
- Calling into a real `pycaw`/Core Audio `IAudioEndpointVolume` COM interface, or
  a real `WmiMonitorBrightness`/`WmiMonitorBrightnessMethods` CIM provider.
- Confirming `_win_security.py`'s DACL construction (`apply_protected_dacl`) and
  verification (`verify_protected_dacl`) against a real NTFS volume -- **DACL
  behavior remains fully unverified against real Windows ACL enforcement.** Every
  claim in `platform_security.py`'s module docstring about what a "protected
  DACL" grants (current user + `LocalSystem`, full control, no inheritance from
  the parent) reflects the code's intent as written and reviewed, not an observed
  outcome on a real filesystem.
- Running `run.ps1`, `run-continuous.ps1`, or `scripts\provision-models.ps1`
  under a real Windows PowerShell host (neither 5.1 nor 7+); their PowerShell
  syntax was checked only by careful manual review in this session, since no
  PowerShell parser (`pwsh`) was available in the (Linux) environment that wrote
  them -- see `docs/reports/M17.md` for exactly what was and was not checked.
- Actually triggering the `windows-compatibility` GitHub Actions workflow.
- Verifying Windows Firewall behavior for real (see the note below).

**Windows Firewall.** `config/windows-full.yaml` and `config/continuous.yaml`
both bind the API to `127.0.0.1` (loopback) only, exactly like every other
tracked config -- a loopback-only listener should not trigger a Windows Defender
Firewall "allow this app" prompt at all, since it never accepts a connection from
outside the local machine. This is standard, well-documented Windows Firewall
behavior (loopback traffic is not filtered by the inbound rule set the "allow
an app" prompt manages), not something specific to this project, but it has not
been observed on a real Windows host in this environment. If you use your own
private, gitignored config to bind beyond loopback (`bind_host` other than
`127.0.0.1`, which additionally requires an explicit `auth_token` --
`APIConfig`'s fail-closed validator, unchanged on Windows), expect a Windows
Defender Firewall prompt on first run, and treat that exposure with the same
caution `docs/RUNNING_ON_UBUNTU.md` §7 and `docs/THREAT_MODEL.md` describe for
any non-loopback deployment -- this project provides no additional Windows-
specific network protection beyond the existing loopback-default/fail-closed-
auth design.

What **was** done before this guide was committed: reading the real M17 source
rather than assuming Windows behavior; checking the locked dependency graph;
compiling the source; running repository-wide Ruff lint and strict mypy; checking
formatting on every changed Python file; and validating the Git diff. See
`docs/reports/M17.md` for the exact evidence and the later Windows workflow result.
