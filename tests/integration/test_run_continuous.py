"""Verifies run-continuous.sh: a launcher that always starts the long-running
`serve` command through run.sh/uv, never falling back to the safe one-shot
`check`, and never duplicating run.sh's bootstrap/uv-install/signal logic.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from face_profile.config import load_config

_REPO_ROOT = Path(__file__).parents[2]
_SCRIPT = _REPO_ROOT / "run-continuous.sh"
_RUN_SH = _REPO_ROOT / "run.sh"
_CONTINUOUS_CONFIG = _REPO_ROOT / "config" / "continuous.yaml"


def test_run_continuous_script_exists_and_is_executable() -> None:
    assert _SCRIPT.is_file(), "run-continuous.sh must exist at the repository root"
    mode = _SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "run-continuous.sh must be executable"


def test_run_sh_still_defaults_to_the_safe_one_shot_check() -> None:
    text = _RUN_SH.read_text(encoding="utf-8")
    assert "pass_args=(check)" in text, "run.sh must remain the unchanged safe one-shot launcher"


def test_run_continuous_delegates_to_run_sh_without_duplicating_bootstrap() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "run.sh" in text
    assert "uv sync" not in text, "bootstrap must stay solely in run.sh, not be duplicated"
    assert "install.sh" not in text, "the uv-install flow must stay solely in run.sh"
    assert "exec " in text, "must exec into run.sh to preserve signal/exit propagation"


def test_run_continuous_forwards_install_uv_flag() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "--install-uv" in text


def test_continuous_config_is_safe_loopback_api_only() -> None:
    assert _CONTINUOUS_CONFIG.is_file(), "config/continuous.yaml must ship in the repository"
    config = load_config(_CONTINUOUS_CONFIG)

    assert config.api.enabled is True
    assert config.api.bind_host == "127.0.0.1"
    assert config.api.auth_token is None

    for name in (
        "camera",
        "detection",
        "tracking",
        "quality",
        "embedding",
        "database",
        "recognition",
        "enrollment",
        "active_user",
        "preference_learning",
        "ui",
        "liveness",
    ):
        section = getattr(config, name)
        assert section.enabled is False, f"{name}.enabled must stay disabled by default"

    assert config.enrollment.automatic_promotion is False
    assert config.settings.backend == "mock"
    assert config.camera.source == "mock"
    assert config.detection.backend == "mock"
    assert config.embedding.backend == "mock"


@pytest.fixture
def fake_uv_bin(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in `uv` that records its invocations instead of touching the network."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log_path = tmp_path / "uv-calls.log"
    fake_uv_path = bin_dir / "uv"
    fake_uv_path.write_text(
        '#!/usr/bin/env bash\necho "$@" >> "$UV_CALL_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    fake_uv_path.chmod(0o755)
    return bin_dir, log_path


def _run(
    args: list[str], *, cwd: Path, bin_dir: Path, log_path: Path
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["UV_CALL_LOG"] = str(log_path)
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_run_continuous_locates_repo_and_runs_serve_regardless_of_cwd(
    tmp_path: Path, fake_uv_bin: tuple[Path, Path]
) -> None:
    bin_dir, log_path = fake_uv_bin
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()

    result = _run([], cwd=other_cwd, bin_dir=bin_dir, log_path=log_path)

    assert result.returncode == 0, result.stderr
    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "sync --locked --all-groups"
    assert calls[-1] == f"run face-profile --config {_CONTINUOUS_CONFIG} serve"


def test_run_continuous_respects_config_override(
    tmp_path: Path, fake_uv_bin: tuple[Path, Path]
) -> None:
    bin_dir, log_path = fake_uv_bin
    custom_config = tmp_path / "custom.yaml"
    custom_config.write_text("api:\n  enabled: true\n", encoding="utf-8")

    result = _run(
        ["--config", str(custom_config)], cwd=tmp_path, bin_dir=bin_dir, log_path=log_path
    )

    assert result.returncode == 0, result.stderr
    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert calls[-1] == f"run face-profile --config {custom_config} serve"


def test_run_continuous_rejects_unexpected_positional_arguments(
    tmp_path: Path, fake_uv_bin: tuple[Path, Path]
) -> None:
    bin_dir, log_path = fake_uv_bin

    result = _run(["check"], cwd=tmp_path, bin_dir=bin_dir, log_path=log_path)

    assert result.returncode != 0
    assert not log_path.exists(), "must fail before ever invoking uv/run.sh"
