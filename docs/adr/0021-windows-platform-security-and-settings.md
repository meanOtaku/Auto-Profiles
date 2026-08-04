# ADR 0021: Windows Platform Security and Settings

- Status: Accepted
- Date: 2026-08-04
- Decision milestone: M17

## Context

The core Windows platform adapters -- `settings/windows.py`'s `WindowsSettingsAdapter`
(pycaw Core Audio volume, WMI/PowerShell CIM brightness), `platform_security.py`'s
cross-platform owner-only file/directory boundary with `_win_security.py`'s DACL
backend, and the Windows branches added to `camera/__init__.py`,
`camera/diagnostics.py`, `diagnostics/preflight.py`, and `settings/factory.py` --
were implemented in the commit immediately preceding this one (`ff09351`, "feat: add
Windows platform adapters"). That commit gives Windows a real settings backend and a
real file/directory security boundary, but no operator could actually *use* any of
it from a checkout: there was no PowerShell launcher equivalent to `run.sh`/
`run-continuous.sh`, no Windows-flavored tracked config, no model-provisioning path
that works without bash, and no documentation telling a Windows operator how to
install, provision models, or interpret the platform-specific failure modes (camera
privacy settings, DACL enforcement, WMI brightness scope) those adapters introduce.

This ADR records the decisions made while closing that gap: the operator-facing
surface (`run.ps1`, `run-continuous.ps1`, `scripts/provision-models.ps1`,
`config/windows-safe.yaml`, `config/windows-full.yaml`,
`.github/workflows/windows-compatibility.yml`, `docs/RUNNING_ON_WINDOWS.md`). It
does not re-decide anything about the adapters' own internals (already implemented
and out of scope for this ADR to revisit) beyond recording their externally-visible
security/behavioral contract as consumed by this new surface.

## Decision

**Native PowerShell, not a bash-under-WSL wrapper.** `run.ps1` and
`run-continuous.ps1` are plain `.ps1` scripts that do not shell out to WSL, Git
Bash, or Cygwin, and target both Windows PowerShell 5.1 (built into Windows 10/11)
and PowerShell 7+ by avoiding version-7-only syntax. A WSL-based approach was
rejected: it would exercise the *Linux* code paths (`settings/linux.py`,
`platform_security.py`'s POSIX branch) inside a Windows host, not the actual
Windows adapters (`settings/windows.py`, the DACL branch) this milestone exists to
give an operator surface to.

**`run.ps1` defaults to a non-mutating preflight of `config/windows-full.yaml`.** This is a
deliberate asymmetry with `run.sh` (whose default `config/default.yaml` is fully
hardware-disabled) and is the one place this milestone knowingly departs from this
project's otherwise-universal "disabled by default" convention for a *launcher's*
default. The reasoning: `run.sh`'s safe default exists because a fresh Ubuntu
checkout has no other obvious "first thing to run" that demonstrates real
capability, and Linux's mock-first culture treats every real adapter as an
explicit, separately-documented opt-in. For Windows specifically, the owner's
request was for a "practical" operator config as the flagship M17 deliverable,
not merely another safe-check equivalent -- `config/windows-safe.yaml` already
exists as that hardware-free equivalent and is one flag away
(`-Config config\windows-safe.yaml`). To avoid this being a silent trap, `run.ps1`
prints an explicit `WARNING:` line every time it falls back to this config,
`config/windows-full.yaml`'s own header comment states the asymmetry, and
`docs/RUNNING_ON_WINDOWS.md` §3 leads with it before any other content. `run-
continuous.ps1`, by contrast, keeps `config/continuous.yaml` (safe) as its own
default -- an unattended, auto-restarting loop is judged a worse place for a
surprising real-hardware default than a single foreground `run.ps1` invocation a
human is actively watching.

**Platform selection remains factory-guarded.** `settings.backend: linux` is
rejected off Linux and `settings.backend: windows` is rejected off Windows.
The mock backend remains portable and the default in the base configuration, so
the Windows work does not replace or weaken the existing Linux adapter.

**Windows settings are transactional and fail before mutation when incomplete.**
Volume uses pycaw's default Core Audio endpoint; brightness uses bounded
PowerShell/CIM calls to `WmiMonitorBrightness`. A full profile contains both
values, so unavailable volume or brightness rejects the whole apply before any
write. If brightness fails after volume succeeds, the adapter attempts to restore
the previous volume and reports rollback failure explicitly. External-monitor-
only systems commonly lack the WMI brightness provider and therefore fail the
full preflight rather than receiving a partial profile.

**Sensitive Windows storage uses protected NTFS DACLs, not POSIX mode bits.**
The DACL grants full control only to the current process-token user and
LocalSystem, is protected from parent inheritance, and carries inheritable child
ACEs on sensitive directories so SQLite WAL/SHM files inherit the same boundary.
Symlinks/reparse points are rejected. This design is fail-closed but remains
unverified on a real NTFS host. Two creation-time limitations are explicit:
Python's Windows `os.open()` lacks an atomic no-follow flag, and a newly created
directory briefly inherits its parent ACL before the protected DACL is applied.
A native `CreateDirectoryW` + `SECURITY_ATTRIBUTES` boundary is required to
remove the latter race; neither limitation is concealed.

**`config/windows-full.yaml` does not enable automatic candidate promotion.**
`config/jetson-full.yaml` sets `enrollment.automatic_promotion: true` behind an
explicit owner-authored consent acknowledgement specific to that request
(`docs/reports/M16.md`). No equivalent owner instruction exists for the Windows
surface, so `config/windows-full.yaml` keeps `automatic_promotion: false` --
manual `face-profile candidate approve`/`reject` review remains required for
every candidate. This is a considered scope reduction, not an oversight: asserting
operator authority/consent for unattended biometric enrollment on a new platform,
on the strength of an ADR alone rather than an explicit owner request, was judged
the wrong default to ship.

**Windows Firewall gets a documented expectation, not new code.** Both
`config/windows-full.yaml` and `config/continuous.yaml` already bind to
`127.0.0.1` only; loopback-only listeners do not trigger Windows Defender
Firewall's inbound "allow this app" prompt, since that prompt guards
network-reachable listeners, not loopback. No firewall-specific code was added --
this is standard OS behavior, documented in `docs/RUNNING_ON_WINDOWS.md` §9 as an
unverified-on-real-hardware expectation, not a new control this project
implements or claims to enforce.

**`scripts/provision-models.ps1` resolves `-DestDir` relative to the repository
root, not the caller's working directory** -- unlike `scripts/provision-models.sh`,
which resolves `DEST_DIR` relative to the caller's CWD. This is a deliberate,
documented improvement made possible because the PowerShell script (like
`run.ps1`) resolves its own script path first; it was judged worth the small
behavioral divergence from the bash script rather than reproducing a
CWD-dependent behavior with no corresponding advantage on Windows.

**No fake Windows Service integration.** `run-continuous.ps1` implements a
self-contained foreground restart loop (bounded exponential backoff, capped at
60s, reset after a stable run) rather than a single `exec`-and-rely-on-an-external-
supervisor script like `run-continuous.sh`, because Windows has no systemd-
equivalent convention this project can assume. It explicitly does not register a
`New-Service`/`sc.exe`/NSSM-style real Windows Service, does not auto-start on
boot, and makes no guarantee of surviving a terminal close, logoff, or reboot --
`docs/RUNNING_ON_WINDOWS.md` §6 states this plainly rather than implying
service-like durability the script does not provide.

**The isolated `windows-compatibility` GitHub Actions workflow never runs
`pytest`.** It is a static-verification and packaging gate
(dependency sync, `compileall`, Ruff, strict mypy, `uv build`, installing the
built wheel into a clean environment, then the hardware-disabled
`config/windows-safe.yaml` check) on a real `windows-latest` GitHub-hosted
runner. On `alpha`, a dedicated marker-only push triggers it while `ci.yml`
ignores only that marker path. Run 30884122482 passed both Python 3.11 and 3.12
matrix entries. This project's convention (`CODING_STANDARDS.md`) is not to
claim any broader hardware behavior from that safe-path evidence.

## Consequences

- A Windows operator now has a native source path intended to install, provision
  models, run a hardware-free check, and launch the full pipeline without WSL.
  Packaging and the hardware-disabled CLI path are Windows-runner verified;
  hardware behavior remains unverified until the checks recorded in the M17
  report pass.
- `run.ps1`'s default-config asymmetry with `run.sh` is a one-time documented
  exception to this project's "disabled by default" convention, justified above;
  it should not be read as license to default other future launchers to a
  hardware-enabled config without equivalent justification and equivalent
  warning-banner treatment.
- `config/windows-full.yaml`'s automatic-promotion default (`false`) means the
  Windows full path is, on this one axis, more conservative than the Jetson full
  path -- an intentional asymmetry, not a parity gap to "fix" without a fresh
  owner instruction authorizing automatic biometric enrollment on Windows.
- Every claim in this ADR and in `docs/RUNNING_ON_WINDOWS.md` about actual runtime
  behavior on Windows (camera open/DirectShow, pycaw Core Audio, WMI brightness,
  DACL enforcement, Windows Firewall prompts, PowerShell script execution under a
  real host) remains unverified against real hardware -- see
  `docs/reports/M17.md` for the exact, itemized list of what was and was not
  checked in this session, and consider this ADR's operational claims provisional
  until the `windows-compatibility` workflow (or a real Windows host) has actually
  produced evidence.
