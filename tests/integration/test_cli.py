import io
import json
from pathlib import Path

import cv2
import numpy as np
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


def test_cli_check_opens_configured_image_source(tmp_path: Path) -> None:
    from face_profile.cli import main

    image_path = tmp_path / "fixture.png"
    assert cv2.imwrite(str(image_path), np.zeros((2, 2, 3), dtype=np.uint8))
    config = tmp_path / "config.yaml"
    config.write_text(
        f"camera:\n  enabled: true\n  source: image\n  path: {image_path}\n",
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


def test_cli_reports_unavailable_camera_source_as_operational_failure(tmp_path: Path) -> None:
    from face_profile.cli import main

    config = tmp_path / "config.yaml"
    config.write_text(
        f"camera:\n  enabled: true\n  source: image\n  path: {tmp_path / 'missing.png'}\n",
        encoding="utf-8",
    )
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


def test_cli_detects_one_frame_and_writes_private_debug_output(tmp_path: Path) -> None:
    from face_profile.cli import main

    image_path = tmp_path / "fixture.png"
    assert cv2.imwrite(str(image_path), np.zeros((32, 32, 3), dtype=np.uint8))
    debug_path = tmp_path / "debug.png"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"camera:\n  enabled: true\n  source: image\n  path: {image_path}\n"
        "detection:\n  enabled: true\n  backend: mock\n",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["--config", str(config), "detect", "--debug-output", str(debug_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    event = json.loads(stdout.getvalue())
    assert event["event_type"] == "DetectionCompleted"
    assert event["face_count"] == 0
    assert debug_path.stat().st_mode & 0o077 == 0
    assert stderr.getvalue() == ""


def test_cli_reports_cleanup_failure_when_detection_and_close_both_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_profile.cli as cli

    calls: list[str] = []

    class FailingSource:
        def open(self) -> None:
            calls.append("open")

        def read(self) -> None:
            calls.append("read")
            raise OSError("private detection details")

        def close(self) -> None:
            calls.append("close")
            raise OSError("private cleanup details")

    config = tmp_path / "config.yaml"
    config.write_text(
        "camera:\n  enabled: true\n  source: mock\ndetection:\n  enabled: true\n  backend: mock\n",
        encoding="utf-8",
    )
    stderr = io.StringIO()
    monkeypatch.setattr(cli, "create_frame_source", lambda config: FailingSource())

    exit_code = cli.main(
        ["--config", str(config), "detect"],
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert exit_code == 4
    assert calls == ["open", "read", "close"]
    event = json.loads(stderr.getvalue())
    assert event["event_type"] == "DetectionFailed"
    assert event["error_code"] == "detection_cleanup_failed"
    assert "private" not in stderr.getvalue().lower()


def test_cli_preflight_passes_for_an_all_disabled_config(tmp_path: Path) -> None:
    from face_profile.cli import main

    config = tmp_path / "config.yaml"
    config.write_text(
        "camera:\n  enabled: false\nlogging:\n  level: INFO\nsettings:\n  backend: mock\n",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--config", str(config), "preflight"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    check_events = [e for e in events if e["event_type"] == "PreflightCheck"]
    assert len(check_events) == 4
    assert all("=skipped" in e["message"] for e in check_events)
    assert events[-1]["event_type"] == "PreflightCompleted"
    assert stderr.getvalue() == ""


def test_cli_preflight_fails_closed_when_a_pinned_model_is_missing(tmp_path: Path) -> None:
    from face_profile.cli import main

    missing_model = tmp_path / "missing.onnx"
    config = tmp_path / "config.yaml"
    config.write_text(
        "camera:\n  enabled: false\n"
        "detection:\n"
        "  enabled: true\n"
        "  backend: yunet\n"
        f"  model_path: {missing_model}\n"
        f"  model_sha256: {'a' * 64}\n"
        "logging:\n  level: INFO\n"
        "settings:\n  backend: mock\n",
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--config", str(config), "preflight"], stdout=stdout, stderr=stderr)

    assert exit_code == 3
    event = json.loads(stderr.getvalue())
    assert event["event_type"] == "PreflightFailed"
    assert event["error_code"] == "preflight_check_failed"
