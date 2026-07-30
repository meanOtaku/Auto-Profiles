import io
import json
from pathlib import Path

import pytest


def test_cli_check_starts_and_stops_without_camera(tmp_path: Path) -> None:
    from face_profile.cli import main

    config = tmp_path / "config.yaml"
    config.write_text(
        "camera:\n"
        "  enabled: false\n"
        "logging:\n"
        "  level: INFO\n"
        "settings:\n"
        "  volume: 50\n"
        "  brightness: 50\n",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--config", str(config), "check"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["event_type"] for event in events] == ["ServiceStarted", "ServiceStopped"]
    assert stderr.getvalue() == ""


def test_cli_reports_configuration_error_without_traceback(tmp_path: Path) -> None:
    from face_profile.cli import main

    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["--config", str(tmp_path / "missing.yaml"), "check"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    event = json.loads(stderr.getvalue())
    assert event["event_type"] == "StartupFailed"
    assert event["error_code"] == "invalid_configuration"
    assert "Traceback" not in stderr.getvalue()
    assert stdout.getvalue() == ""


def test_cli_reports_missing_camera_adapter_as_operational_failure(tmp_path: Path) -> None:
    from face_profile.cli import main

    config = tmp_path / "config.yaml"
    config.write_text("camera:\n  enabled: true\n", encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--config", str(config), "check"], stdout=stdout, stderr=stderr)

    assert exit_code == 3
    event = json.loads(stderr.getvalue())
    assert event["event_type"] == "StartupFailed"
    assert event["error_code"] == "service_start_failed"
    assert "Traceback" not in stderr.getvalue()


def test_cli_reports_shutdown_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_profile.cli as cli

    class FailingStopService:
        state = "stopped"

        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def start(self) -> None:
            self.state = "running"

        def stop(self) -> None:
            raise OSError("close failed")

    config = tmp_path / "config.yaml"
    config.write_text("camera:\n  enabled: false\n", encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(cli, "Service", FailingStopService)

    exit_code = cli.main(["--config", str(config), "check"], stdout=stdout, stderr=stderr)

    assert exit_code == 4
    event = json.loads(stderr.getvalue())
    assert event["event_type"] == "ShutdownFailed"
    assert event["error_code"] == "service_stop_failed"
    assert "Traceback" not in stderr.getvalue()


def test_cli_attempts_cleanup_after_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_profile.cli as cli

    calls: list[str] = []

    class FailingStartService:
        state = "stopped"

        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def start(self) -> None:
            calls.append("start")
            raise OSError("open failed")

        def stop(self) -> None:
            calls.append("stop")

    config = tmp_path / "config.yaml"
    config.write_text("camera:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(cli, "Service", FailingStartService)

    exit_code = cli.main(
        ["--config", str(config), "check"],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == 3
    assert calls == ["start", "stop"]
