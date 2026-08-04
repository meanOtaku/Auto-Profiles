#!/usr/bin/env python3
"""Cross-platform bootstrap entry point for the Auto-Profiles service.

This is the single supported way to install, verify, and run
``face-profile-system`` on Linux, macOS, or Windows. It intentionally uses
only the Python standard library so it can run before any project
dependency (including ``uv`` itself) is available. Once dependencies are
synchronized it shells out to ``uv run`` for anything that needs the real
application (``face_profile.config.load_config``, ``face-profile serve``,
``face-profile preflight``) instead of importing project code directly, so
this file's own import graph never depends on the synced environment.

Commands::

    python bootstrap.py [run]   # sync (if needed), start `serve`, wait for
                                 # health, open the dashboard, own shutdown
    python bootstrap.py doctor  # read-only diagnostics; never installs,
                                 # syncs, downloads models, or starts anything
    python bootstrap.py setup   # create user config/data dirs, copy the
                                 # safe default config, optionally provision
                                 # models, perform a locked production sync
    python bootstrap.py update  # force-refresh the environment from the
                                 # committed lock file (never `uv lock`,
                                 # never an implicit `git pull`)

Global options: ``--config PATH``, ``--headless``, ``--no-browser``,
``--install-uv``. See ``RUN_DEPLOY.md`` for the full operator guide.

``run.sh``, ``run.ps1``, ``run-continuous.sh``, ``run-continuous.ps1``, and
``scripts/provision-models.{sh,ps1}`` are retired; this module is now the
only cross-platform foreground launcher, dependency bootstrapper, model
provisioner, readiness monitor, and shutdown owner.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

_HEALTH_TIMEOUT_SECONDS = 30.0
_HEALTH_POLL_MIN_DELAY = 0.2
_HEALTH_POLL_MAX_DELAY = 2.0
_SHUTDOWN_GRACE_SECONDS = 10.0
_MAX_INSTALLER_BYTES = 2 * 1024 * 1024
_MAX_MODEL_BYTES = 200 * 1024 * 1024

_CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_CTRL_BREAK_EVENT: int = getattr(signal, "CTRL_BREAK_EVENT", 0)


class BootstrapError(RuntimeError):
    """Raised for any expected bootstrap failure; always caught at the CLI boundary."""


class DoctorStatus(StrEnum):
    """Outcome of one read-only doctor check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One named doctor check's outcome and a human-readable detail string."""

    name: str
    status: DoctorStatus
    detail: str


@dataclass(frozen=True, slots=True)
class Paths:
    """Resolved repository and OS-private data locations for one run."""

    repo_root: Path
    venv_dir: Path
    state_path: Path


@dataclass(frozen=True, slots=True)
class ApiTarget:
    """The loaded configuration's API bind target, read via the real loader."""

    host: str
    port: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One pinned model artifact from ``model-manifest.json``."""

    name: str
    url: str
    sha256: str
    size_bytes: int


# --------------------------------------------------------------------------
# Repository and OS path resolution
# --------------------------------------------------------------------------


def find_repo_root() -> Path:
    """Return the directory containing this script, verified as the repo root.

    Independent of the caller's current working directory, matching the
    convention the retired ``run.sh``/``run.ps1`` launchers used.
    """

    candidate = Path(__file__).resolve().parent
    pyproject = candidate / "pyproject.toml"
    if not pyproject.is_file() or 'name = "face-profile-system"' not in pyproject.read_text(
        encoding="utf-8"
    ):
        raise BootstrapError(
            "could not locate the face-profile-system repository root next to this "
            f"script (expected {pyproject})"
        )
    return candidate


def default_os_paths() -> tuple[Path, Path]:
    """Return ``(default_config_path, data_dir)`` for this OS, without creating them.

    Linux: ``${XDG_CONFIG_HOME:-~/.config}/auto-profiles`` and
    ``${XDG_DATA_HOME:-~/.local/share}/auto-profiles``. macOS: both rooted at
    ``~/Library/Application Support/Auto-Profiles``. Windows: ``%APPDATA%``
    and ``%LOCALAPPDATA%`` under ``Auto-Profiles``.
    """

    system = platform.system()
    home = Path.home()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        local_appdata = os.environ.get("LOCALAPPDATA")
        config_root = Path(appdata) if appdata else home / "AppData" / "Roaming"
        data_root = Path(local_appdata) if local_appdata else home / "AppData" / "Local"
        return config_root / "Auto-Profiles" / "config.yaml", data_root / "Auto-Profiles"
    if system == "Darwin":
        base = home / "Library" / "Application Support" / "Auto-Profiles"
        return base / "config.yaml", base
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    xdg_data = os.environ.get("XDG_DATA_HOME")
    config_root = Path(xdg_config) if xdg_config else home / ".config"
    data_root = Path(xdg_data) if xdg_data else home / ".local" / "share"
    return config_root / "auto-profiles" / "config.yaml", data_root / "auto-profiles"


def ensure_dir_private(path: Path) -> None:
    """Create ``path`` (with parents), owner-only where the platform supports it.

    POSIX gets ``0700`` immediately. Windows has no POSIX mode bits; applying
    the same protected-DACL boundary ``platform_security.py`` gives the
    running service requires ``pywin32``, which is not available before this
    script's own stdlib-only dependency sync, so directory hardening on
    Windows is deferred to the service itself once it opens the database and
    key files under this directory -- a documented limitation, not a silent
    gap.
    """

    path.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Windows":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)


def resolve_config_path(
    args: argparse.Namespace, repo_root: Path, default_config_path: Path
) -> Path:
    """Choose the config file for ``run``/``doctor``: explicit, user, then bundled."""

    if args.config is not None:
        path = Path(args.config).expanduser().resolve()
        if not path.is_file():
            raise BootstrapError(f"config file not found: {path}")
        return path
    if default_config_path.is_file():
        return default_config_path
    bundled = repo_root / "config" / "default.yaml"
    print(
        f"no user config found at {default_config_path}; using bundled {bundled} "
        "(run 'python bootstrap.py setup' to create a persistent, editable copy)"
    )
    return bundled


# --------------------------------------------------------------------------
# uv discovery and explicit-only install
# --------------------------------------------------------------------------


def _uv_install_command() -> str:
    if platform.system() == "Windows":
        return 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    return "curl -LsSf https://astral.sh/uv/install.sh | sh"


def _uv_version(uv_path: str | None = None) -> str | None:
    executable = uv_path or shutil.which("uv")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _https_download(url: str, *, max_bytes: int) -> bytes:
    """Download ``url`` fully into memory, refusing non-HTTPS sources or redirects."""

    if not url.startswith("https://"):
        raise BootstrapError(f"refusing non-HTTPS URL: {url}")
    with urllib.request.urlopen(url, timeout=60) as response:
        final_url = response.geturl()
        if not final_url.startswith("https://"):
            raise BootstrapError(f"redirect left HTTPS for {url} -> {final_url}; refusing")
        data: bytes = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BootstrapError(f"download from {url} exceeded the maximum allowed size")
    return data


def install_uv() -> None:
    """Download and run the official uv installer. Only called with explicit consent."""

    system = platform.system()
    url = (
        "https://astral.sh/uv/install.ps1"
        if system == "Windows"
        else "https://astral.sh/uv/install.sh"
    )
    print(f"downloading the official uv installer over HTTPS from {url}")
    script_bytes = _https_download(url, max_bytes=_MAX_INSTALLER_BYTES)
    suffix = ".ps1" if system == "Windows" else ".sh"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(script_bytes)
        script_path = Path(handle.name)
    try:
        print(f"running the downloaded installer: {script_path}")
        command = (
            ["powershell", "-ExecutionPolicy", "ByPass", "-File", str(script_path)]
            if system == "Windows"
            else ["sh", str(script_path)]
        )
        result = subprocess.run(command)
        if result.returncode != 0:
            raise BootstrapError(f"uv installer exited with code {result.returncode}")
    finally:
        script_path.unlink(missing_ok=True)
    if shutil.which("uv") is None:
        hint = Path.home() / ".local" / "bin"
        raise BootstrapError(
            "uv installation finished but 'uv' is still not on PATH; add "
            f"{hint} to PATH (or the location the installer printed) and open a new "
            "shell, then re-run bootstrap.py"
        )


def ensure_uv(args: argparse.Namespace) -> str:
    """Return the ``uv`` executable path, installing it only if explicitly approved."""

    found = shutil.which("uv")
    if found is not None:
        return found
    print("uv was not found on PATH.")
    print(f"official installer command: {_uv_install_command()}")
    if not args.install_uv:
        raise BootstrapError(
            "uv is required. Install it yourself with the command above, or re-run "
            "with --install-uv to have bootstrap.py download and run the official "
            "installer for you."
        )
    install_uv()
    found = shutil.which("uv")
    if found is None:
        raise BootstrapError("uv installation finished but 'uv' is still not on PATH")
    return found


# --------------------------------------------------------------------------
# Fingerprinted, production-only dependency sync
# --------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_fingerprint(repo_root: Path) -> dict[str, str]:
    """Return the values that must all match for a synced environment to stay valid."""

    return {
        "pyproject_sha256": _sha256_file(repo_root / "pyproject.toml"),
        "uv_lock_sha256": _sha256_file(repo_root / "uv.lock"),
        "python_executable": sys.executable,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "uv_version": _uv_version() or "",
        "platform": platform.system(),
        "machine": platform.machine(),
    }


def load_state(paths: Paths) -> dict[str, object] | None:
    if not paths.state_path.is_file():
        return None
    try:
        loaded = json.loads(paths.state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def is_environment_current(repo_root: Path, paths: Paths) -> bool:
    """Read-only: compare the stored fingerprint against the current one. Never syncs."""

    if not (paths.venv_dir / "pyvenv.cfg").is_file():
        return False
    state = load_state(paths)
    if state is None:
        return False
    stored_fingerprint = state.get("fingerprint")
    if not isinstance(stored_fingerprint, dict):
        return False
    return stored_fingerprint == compute_fingerprint(repo_root)


def write_state_atomic(paths: Paths, fingerprint: dict[str, str]) -> None:
    """Write the fingerprint state only after a successful sync, atomically."""

    paths.state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, "synced_at": datetime.now(UTC).isoformat()}
    fd, tmp_name = tempfile.mkstemp(
        dir=paths.state_path.parent, prefix=".bootstrap-state.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, paths.state_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def sync_environment(repo_root: Path, paths: Paths, uv_path: str, *, force: bool) -> None:
    """Run ``uv sync --locked --no-dev`` into the OS data environment, if needed.

    Skipped when ``force`` is false and the fingerprint already matches the
    stored state. ``UV_PROJECT_ENVIRONMENT`` is set so this never creates or
    mutates a repository-local ``.venv``.
    """

    if not force and is_environment_current(repo_root, paths):
        print("environment already current; skipping sync (use 'update' to force)")
        return
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(paths.venv_dir)
    print(f"synchronizing production dependencies into {paths.venv_dir}")
    result = subprocess.run([uv_path, "sync", "--locked", "--no-dev"], cwd=repo_root, env=env)
    if result.returncode != 0:
        raise BootstrapError(f"uv sync --locked --no-dev failed with exit code {result.returncode}")
    write_state_atomic(paths, compute_fingerprint(repo_root))
    print("dependency sync complete")


def _read_api_target(repo_root: Path, paths: Paths, uv_path: str, config_path: Path) -> ApiTarget:
    """Read ``api.bind_host``/``bind_port``/``enabled`` through the real, validated loader."""

    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(paths.venv_dir)
    probe = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from face_profile.config import load_config\n"
        "config = load_config(Path(sys.argv[1]))\n"
        "print(json.dumps({'host': config.api.bind_host, 'port': config.api.bind_port, "
        "'enabled': config.api.enabled}))\n"
    )
    result = subprocess.run(
        [uv_path, "run", "--no-sync", "python", "-c", probe, str(config_path)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise BootstrapError(
            f"could not read API configuration from {config_path}: {result.stderr.strip()}"
        )
    data = json.loads(result.stdout.strip().splitlines()[-1])
    return ApiTarget(host=str(data["host"]), port=int(data["port"]), enabled=bool(data["enabled"]))


# --------------------------------------------------------------------------
# Pinned model provisioning (model-manifest.json)
# --------------------------------------------------------------------------


def load_model_manifest(repo_root: Path) -> tuple[ModelSpec, ...]:
    manifest_path = repo_root / "model-manifest.json"
    if not manifest_path.is_file():
        return ()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return tuple(
        ModelSpec(
            name=str(entry["name"]),
            url=str(entry["url"]),
            sha256=str(entry["sha256"]),
            size_bytes=int(entry["size_bytes"]),
        )
        for entry in data.get("models", [])
    )


def _provision_one_model(dest_dir: Path, spec: ModelSpec) -> None:
    dest = dest_dir / spec.name
    if dest.is_file():
        actual = _sha256_file(dest)
        if hmac.compare_digest(actual, spec.sha256):
            print(f"{spec.name}: already present and verified (sha256 {actual})")
            return
        raise BootstrapError(
            f"{dest} exists but its sha256 ({actual}) does not match the pinned value "
            f"({spec.sha256}); refusing to overwrite an unexpected file"
        )
    if not spec.url.startswith("https://"):
        raise BootstrapError(f"refusing non-HTTPS model URL: {spec.url}")
    print(f"downloading {spec.name} from {spec.url}")
    fd, tmp_name = tempfile.mkstemp(dir=dest_dir, prefix=f".{spec.name}.")
    tmp_path = Path(tmp_name)
    max_bytes = max(spec.size_bytes * 2, _MAX_MODEL_BYTES)
    try:
        with (
            os.fdopen(fd, "wb") as tmp_file,
            urllib.request.urlopen(spec.url, timeout=120) as response,
        ):
            final_url = response.geturl()
            if not final_url.startswith("https://"):
                raise BootstrapError(
                    f"redirect left HTTPS for {spec.name}: {spec.url} -> {final_url}; refusing"
                )
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise BootstrapError(f"{spec.name} exceeded the maximum allowed download size")
                tmp_file.write(chunk)
        actual = _sha256_file(tmp_path)
        if not hmac.compare_digest(actual, spec.sha256):
            raise BootstrapError(
                f"sha256 mismatch for {spec.name}: expected {spec.sha256}, got {actual}; "
                "refusing to install an unverified model file"
            )
        os.replace(tmp_path, dest)
        print(f"{spec.name}: downloaded and verified (sha256 {actual})")
    finally:
        tmp_path.unlink(missing_ok=True)


def provision_models(repo_root: Path, dest_dir: Path) -> None:
    """Download and integrity-verify every model in ``model-manifest.json``."""

    specs = load_model_manifest(repo_root)
    if not specs:
        print("no model-manifest.json found; nothing to provision")
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        _provision_one_model(dest_dir, spec)


# --------------------------------------------------------------------------
# Service lifecycle: readiness, browser, shutdown
# --------------------------------------------------------------------------


def wait_for_health(url: str, *, deadline_seconds: float, child: subprocess.Popen[bytes]) -> None:
    """Poll ``url`` with bounded exponential backoff until it answers 200 OK."""

    start = time.monotonic()
    delay = _HEALTH_POLL_MIN_DELAY
    while True:
        exit_code = child.poll()
        if exit_code is not None:
            raise BootstrapError(
                f"face-profile serve exited with code {exit_code} before becoming healthy "
                "(check the output above; a port collision is a common cause)"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() - start > deadline_seconds:
            raise BootstrapError(f"timed out after {deadline_seconds:.0f}s waiting for {url}")
        time.sleep(delay)
        delay = min(delay * 1.5, _HEALTH_POLL_MAX_DELAY)


def _terminate_child(child: subprocess.Popen[bytes], *, grace_seconds: float) -> int | None:
    """Bounded graceful shutdown: terminate, wait, then kill only as a final fallback."""

    if child.poll() is not None:
        return child.returncode
    if platform.system() == "Windows":
        try:
            child.send_signal(_CTRL_BREAK_EVENT)
        except OSError:
            child.terminate()
    else:
        child.terminate()
    try:
        return child.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        child.kill()
        try:
            return child.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            return None


def _install_signal_forwarding(child: subprocess.Popen[bytes]) -> None:
    """Forward the first SIGINT/SIGTERM as a graceful stop; a second one kills."""

    state = {"signalled": False}

    def handler(signum: int, _frame: object) -> None:
        if not state["signalled"]:
            state["signalled"] = True
            print(f"\nreceived signal {signum}; requesting graceful shutdown of the child")
            if platform.system() == "Windows":
                try:
                    child.send_signal(_CTRL_BREAK_EVENT)
                    return
                except OSError:
                    pass
            child.terminate()
        else:
            print("received a second signal; killing the child")
            child.kill()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def _spawn_serve(
    repo_root: Path, paths: Paths, uv_path: str, config_path: Path
) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(paths.venv_dir)
    command = [uv_path, "run", "--no-sync", "face-profile", "--config", str(config_path), "serve"]
    print(f"starting: {' '.join(command)}")
    if platform.system() == "Windows":
        return subprocess.Popen(
            command, cwd=repo_root, env=env, creationflags=_CREATE_NEW_PROCESS_GROUP
        )
    return subprocess.Popen(command, cwd=repo_root, env=env, start_new_session=True)


# --------------------------------------------------------------------------
# doctor: read-only diagnostics
# --------------------------------------------------------------------------


def _doctor_python_version() -> DoctorCheck:
    name = "python_version"
    major, minor = sys.version_info[:2]
    detail = f"running {major}.{minor} at {sys.executable}"
    if major == 3 and minor in (11, 12):
        return DoctorCheck(name, DoctorStatus.PASS, detail)
    return DoctorCheck(name, DoctorStatus.FAIL, f"{detail}; only Python 3.11/3.12 are supported")


def _doctor_uv_presence() -> DoctorCheck:
    name = "uv_presence"
    version = _uv_version()
    if version is None:
        return DoctorCheck(
            name, DoctorStatus.FAIL, f"uv not found; install with: {_uv_install_command()}"
        )
    return DoctorCheck(name, DoctorStatus.PASS, version)


def _doctor_repository_identity(repo_root: Path) -> DoctorCheck:
    name = "repository_identity"
    required = ("pyproject.toml", "uv.lock", "config/default.yaml", "src/face_profile/cli.py")
    missing = [item for item in required if not (repo_root / item).exists()]
    if missing:
        return DoctorCheck(name, DoctorStatus.FAIL, f"missing expected files: {', '.join(missing)}")
    return DoctorCheck(name, DoctorStatus.PASS, f"{repo_root} verified as face-profile-system")


def _doctor_config(
    args: argparse.Namespace, default_config_path: Path, repo_root: Path
) -> tuple[Path | None, DoctorCheck]:
    name = "config"
    if args.config is not None:
        path = Path(args.config).expanduser().resolve()
        if path.is_file():
            return path, DoctorCheck(name, DoctorStatus.PASS, f"using explicit {path}")
        return None, DoctorCheck(name, DoctorStatus.FAIL, f"explicit --config not found: {path}")
    if default_config_path.is_file():
        return default_config_path, DoctorCheck(
            name, DoctorStatus.PASS, f"using {default_config_path}"
        )
    bundled = repo_root / "config" / "default.yaml"
    if bundled.is_file():
        return bundled, DoctorCheck(
            name,
            DoctorStatus.WARN,
            f"no user config at {default_config_path}; would fall back to bundled "
            f"{bundled} (run 'setup' to create a persistent copy)",
        )
    return None, DoctorCheck(name, DoctorStatus.FAIL, "no explicit, user, or bundled config found")


def _doctor_environment_fingerprint(env_current: bool, paths: Paths) -> DoctorCheck:
    name = "environment_fingerprint"
    state = load_state(paths)
    if state is None:
        return DoctorCheck(
            name,
            DoctorStatus.WARN,
            f"never synced; run 'setup' (state would live at {paths.state_path})",
        )
    if env_current:
        return DoctorCheck(
            name, DoctorStatus.PASS, f"current as of {state.get('synced_at', 'unknown')}"
        )
    return DoctorCheck(
        name,
        DoctorStatus.WARN,
        "stale: pyproject.toml, uv.lock, the interpreter, or uv changed since the last "
        "sync; run 'update'",
    )


def _doctor_user_paths(default_config_path: Path, data_dir: Path) -> DoctorCheck:
    name = "user_paths_accessible"
    problems: list[str] = []
    for label, path in (("config", default_config_path.parent), ("data", data_dir)):
        ancestor = path
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        if not os.access(ancestor, os.W_OK):
            problems.append(f"{label} path {path} is not creatable under {ancestor}")
    if problems:
        return DoctorCheck(name, DoctorStatus.FAIL, "; ".join(problems))
    return DoctorCheck(
        name, DoctorStatus.PASS, f"config={default_config_path.parent}, data={data_dir}"
    )


def _doctor_models(repo_root: Path, data_dir: Path) -> DoctorCheck:
    name = "model_presence_and_hashes"
    specs = load_model_manifest(repo_root)
    if not specs:
        return DoctorCheck(
            name, DoctorStatus.WARN, "no model-manifest.json found; nothing to verify"
        )
    problems: list[str] = []
    ok_count = 0
    for spec in specs:
        candidates = (data_dir / "models" / spec.name, repo_root / "models" / spec.name)
        found = next((candidate for candidate in candidates if candidate.is_file()), None)
        if found is None:
            problems.append(f"{spec.name}: not found (run 'setup --models' or 'update --models')")
            continue
        actual = _sha256_file(found)
        if hmac.compare_digest(actual, spec.sha256):
            ok_count += 1
        else:
            problems.append(f"{spec.name}: sha256 mismatch at {found}")
    if any("mismatch" in problem for problem in problems):
        return DoctorCheck(name, DoctorStatus.FAIL, "; ".join(problems))
    if problems:
        return DoctorCheck(name, DoctorStatus.WARN, "; ".join(problems))
    return DoctorCheck(name, DoctorStatus.PASS, f"{ok_count}/{len(specs)} pinned models verified")


def _doctor_loopback(
    repo_root: Path, paths: Paths, config_path: Path, *, env_current: bool
) -> DoctorCheck:
    name = "loopback_reachability"
    if not env_current:
        return DoctorCheck(
            name, DoctorStatus.WARN, "environment not current; run 'setup' or 'update' first"
        )
    uv_path = shutil.which("uv")
    if uv_path is None:
        return DoctorCheck(
            name, DoctorStatus.WARN, "uv not on PATH; cannot read the configured target"
        )
    try:
        target = _read_api_target(repo_root, paths, uv_path, config_path)
    except (BootstrapError, subprocess.TimeoutExpired) as error:
        return DoctorCheck(name, DoctorStatus.WARN, f"could not read API target: {error}")
    if not target.enabled:
        return DoctorCheck(name, DoctorStatus.PASS, "api.enabled is false; nothing to check")
    try:
        with socket.create_connection((target.host, target.port), timeout=0.5):
            return DoctorCheck(
                name, DoctorStatus.PASS, f"{target.host}:{target.port} is already reachable"
            )
    except OSError:
        return DoctorCheck(
            name,
            DoctorStatus.PASS,
            f"{target.host}:{target.port} not listening (expected when not running)",
        )


def _doctor_installed_preflight(
    repo_root: Path, paths: Paths, config_path: Path, *, env_current: bool
) -> DoctorCheck:
    name = "installed_package_preflight"
    if not env_current:
        return DoctorCheck(
            name, DoctorStatus.WARN, "environment not current; run 'setup' or 'update' first"
        )
    uv_path = shutil.which("uv")
    if uv_path is None:
        return DoctorCheck(name, DoctorStatus.WARN, "uv not on PATH; cannot run 'uv run --no-sync'")
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(paths.venv_dir)
    try:
        result = subprocess.run(
            [
                uv_path,
                "run",
                "--no-sync",
                "face-profile",
                "--config",
                str(config_path),
                "preflight",
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return DoctorCheck(name, DoctorStatus.WARN, "preflight timed out")
    if result.returncode != 0:
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        return DoctorCheck(
            name, DoctorStatus.FAIL, lines[-1] if lines else f"exit code {result.returncode}"
        )
    return DoctorCheck(name, DoctorStatus.PASS, "face-profile preflight passed")


def _doctor_platform_notes() -> DoctorCheck:
    name = "platform_notes"
    system = platform.system()
    if system == "Windows":
        return DoctorCheck(
            name,
            DoctorStatus.WARN,
            "camera validation is deferred to runtime capture on Windows; NTFS DACL "
            "hardening is applied by the running service, not by bootstrap.py",
        )
    if system == "Darwin":
        return DoctorCheck(
            name,
            DoctorStatus.WARN,
            "no real macOS settings adapter exists yet; settings.backend must stay 'mock'",
        )
    return DoctorCheck(name, DoctorStatus.PASS, f"{system}: no additional platform limitations")


def run_doctor_checks(args: argparse.Namespace, repo_root: Path, paths: Paths) -> list[DoctorCheck]:
    default_config_path, data_dir = default_os_paths()
    checks = [
        _doctor_python_version(),
        _doctor_uv_presence(),
        _doctor_repository_identity(repo_root),
    ]
    config_path, config_check = _doctor_config(args, default_config_path, repo_root)
    checks.append(config_check)
    env_current = is_environment_current(repo_root, paths)
    checks.append(_doctor_environment_fingerprint(env_current, paths))
    checks.append(_doctor_user_paths(default_config_path, data_dir))
    checks.append(_doctor_models(repo_root, data_dir))
    if config_path is not None:
        checks.append(_doctor_loopback(repo_root, paths, config_path, env_current=env_current))
        checks.append(
            _doctor_installed_preflight(repo_root, paths, config_path, env_current=env_current)
        )
    checks.append(_doctor_platform_notes())
    return checks


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _paths_for(repo_root: Path, data_dir: Path) -> Paths:
    return Paths(
        repo_root=repo_root,
        venv_dir=data_dir / "venv",
        state_path=data_dir / "bootstrap-state.json",
    )


def cmd_run(args: argparse.Namespace) -> int:
    repo_root = find_repo_root()
    default_config_path, data_dir = default_os_paths()
    config_path = resolve_config_path(args, repo_root, default_config_path)
    uv_path = ensure_uv(args)
    paths = _paths_for(repo_root, data_dir)
    ensure_dir_private(data_dir)
    sync_environment(repo_root, paths, uv_path, force=False)

    target = _read_api_target(repo_root, paths, uv_path, config_path)
    if not target.enabled:
        raise BootstrapError(f"api.enabled is false in {config_path}; nothing to serve")

    child = _spawn_serve(repo_root, paths, uv_path, config_path)
    _install_signal_forwarding(child)
    health_url = f"http://{target.host}:{target.port}/api/v1/health"
    try:
        wait_for_health(health_url, deadline_seconds=_HEALTH_TIMEOUT_SECONDS, child=child)
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        _terminate_child(child, grace_seconds=_SHUTDOWN_GRACE_SECONDS)
        return 1

    dashboard_url = f"http://{target.host}:{target.port}/"
    print(f"face-profile is healthy: {dashboard_url}")
    if not args.headless and not args.no_browser:
        webbrowser.open(dashboard_url)

    exit_code = child.wait()
    return exit_code


def cmd_doctor(args: argparse.Namespace) -> int:
    repo_root = find_repo_root()
    _, data_dir = default_os_paths()
    paths = _paths_for(repo_root, data_dir)
    checks = run_doctor_checks(args, repo_root, paths)
    for check in checks:
        print(f"[{check.status.value.upper():4}] {check.name}: {check.detail}")
    return 1 if any(check.status is DoctorStatus.FAIL for check in checks) else 0


def write_user_config(repo_root: Path, dest: Path, data_dir: Path) -> None:
    """Copy the safe bundled default config, rewriting database/key paths absolute.

    Never overwrites an existing file; callers must check first. Absolute
    paths avoid this project's pre-bootstrap ambiguity where relative YAML
    paths were interpreted against the launcher's working directory rather
    than the config file's own location.
    """

    source = repo_root / "config" / "default.yaml"
    text = source.read_text(encoding="utf-8")
    db_path = data_dir / "face_profile.sqlite3"
    key_path = data_dir / "face_profile.key"
    text = text.replace("path: data/face_profile.sqlite3", f"path: {db_path.as_posix()}")
    text = text.replace("key_path: data/face_profile.key", f"key_path: {key_path.as_posix()}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".config.", suffix=".yaml.tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, dest)
    finally:
        tmp_path.unlink(missing_ok=True)


def _prompt_yes_no(question: str) -> bool:
    try:
        reply = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in {"y", "yes"}


def cmd_setup(args: argparse.Namespace) -> int:
    repo_root = find_repo_root()
    default_config_path, data_dir = default_os_paths()
    ensure_dir_private(default_config_path.parent)
    ensure_dir_private(data_dir)

    if default_config_path.is_file():
        print(f"config already exists at {default_config_path}; leaving it unchanged")
    else:
        write_user_config(repo_root, default_config_path, data_dir)
        print(f"wrote safe default config to {default_config_path}")

    should_provision = bool(args.models)
    if not should_provision and not args.headless and sys.stdin.isatty():
        should_provision = _prompt_yes_no("Download and verify the pinned models now?")
    if should_provision:
        provision_models(repo_root, data_dir / "models")
    else:
        print("skipping model provisioning (pass --models to opt in)")

    uv_path = ensure_uv(args)
    paths = _paths_for(repo_root, data_dir)
    sync_environment(repo_root, paths, uv_path, force=True)
    print("setup complete")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    repo_root = find_repo_root()
    _, data_dir = default_os_paths()
    paths = _paths_for(repo_root, data_dir)
    uv_path = ensure_uv(args)
    sync_environment(repo_root, paths, uv_path, force=True)
    if args.models:
        provision_models(repo_root, data_dir / "models")
    print("update complete")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_COMMANDS = ("run", "doctor", "setup", "update")
_VALUE_OPTIONS = ("--config",)


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    """Append the implicit ``run`` command when none was given (no aliasing needed).

    Global options are accepted both before and after the subcommand (e.g.
    ``bootstrap.py --config x.yaml run`` and ``bootstrap.py run --config
    x.yaml`` both work), so this has to walk ``argv`` value-aware -- a bare
    scan for "the first token that doesn't start with '-'" would mistake
    ``--config``'s own value for an already-given command.
    """

    tokens = list(argv)
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _COMMANDS:
            return tokens
        if token in _VALUE_OPTIONS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        # An unrecognized positional-looking token: let argparse produce its
        # own clear "invalid choice" error rather than guessing further.
        return tokens
    return [*tokens, "run"]


def _add_common_options(target: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    """Register the global options on ``target``.

    ``suppress_defaults=True`` is used for each subparser's own copy so an
    option given only at the top level (e.g. ``bootstrap.py --headless
    run``) is not silently reset back to its default once the chosen
    subparser merges its own results into the same namespace.
    """

    unset_path = argparse.SUPPRESS if suppress_defaults else None
    unset_flag = argparse.SUPPRESS if suppress_defaults else False
    target.add_argument(
        "--config",
        type=Path,
        default=unset_path,
        help="Config YAML path (default: the OS user config, falling back to config/default.yaml)",
    )
    target.add_argument(
        "--headless",
        action="store_true",
        default=unset_flag,
        help="Disable interactive prompts and browser launch; automation-friendly output",
    )
    target.add_argument(
        "--no-browser",
        action="store_true",
        default=unset_flag,
        help="Run normally but never open a browser",
    )
    target.add_argument(
        "--install-uv",
        action="store_true",
        default=unset_flag,
        help="Pre-approve downloading and running the official uv installer if uv is missing",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap.py",
        description="Cross-platform bootstrap for the Auto-Profiles / face-profile-system service.",
    )
    _add_common_options(parser, suppress_defaults=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Sync if needed, start `serve`, wait for health, open the dashboard"
    )
    _add_common_options(run_parser, suppress_defaults=True)
    doctor_parser = subparsers.add_parser(
        "doctor", help="Read-only diagnostics; never installs or starts anything"
    )
    _add_common_options(doctor_parser, suppress_defaults=True)
    setup_parser = subparsers.add_parser(
        "setup", help="Create user config/data directories and synchronize dependencies"
    )
    _add_common_options(setup_parser, suppress_defaults=True)
    setup_parser.add_argument(
        "--models", action="store_true", help="Also download and verify pinned models"
    )
    update_parser = subparsers.add_parser(
        "update", help="Force-refresh the environment from the committed lock file"
    )
    _add_common_options(update_parser, suppress_defaults=True)
    update_parser.add_argument(
        "--models", action="store_true", help="Also re-download and verify pinned models"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(raw_argv))
    if not hasattr(args, "models"):
        args.models = False
    try:
        if args.command == "run":
            return cmd_run(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "setup":
            return cmd_setup(args)
        if args.command == "update":
            return cmd_update(args)
        raise BootstrapError(f"unknown command: {args.command}")
    except BootstrapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
