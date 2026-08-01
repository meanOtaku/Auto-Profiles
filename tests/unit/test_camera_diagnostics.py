import os
import platform

import pytest


def test_diagnose_webcam_reports_generic_hint_on_non_linux_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.camera.diagnostics import diagnose_webcam

    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    result = diagnose_webcam(0)

    assert result.device_path is None
    assert result.device_exists is None
    assert result.permission_ok is None
    assert "Linux-only" in result.hint


def test_diagnose_webcam_reports_missing_device_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.camera.diagnostics import diagnose_webcam

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(os.path, "exists", lambda _path: False)

    result = diagnose_webcam(3)

    assert result.device_path == "/dev/video3"
    assert result.device_exists is False
    assert result.permission_ok is None
    assert "does not exist" in result.hint
    assert "v4l2-ctl --list-devices" in result.hint


def test_diagnose_webcam_reports_permission_denied_and_video_group_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.camera import diagnostics

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(os.path, "exists", lambda _path: True)
    monkeypatch.setattr(os, "access", lambda _path, _mode: False)
    monkeypatch.setattr(diagnostics, "_user_in_video_group", lambda: False)

    result = diagnostics.diagnose_webcam(0)

    assert result.device_path == "/dev/video0"
    assert result.device_exists is True
    assert result.permission_ok is False
    assert "not in the 'video' group" in result.hint
    assert "usermod -aG video" in result.hint


def test_diagnose_webcam_reports_permission_denied_without_group_claim_when_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.camera import diagnostics

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(os.path, "exists", lambda _path: True)
    monkeypatch.setattr(os, "access", lambda _path, _mode: False)
    monkeypatch.setattr(diagnostics, "_user_in_video_group", lambda: None)

    result = diagnostics.diagnose_webcam(0)

    assert result.permission_ok is False
    assert "not in the 'video' group" not in result.hint
    assert "usermod -aG video" in result.hint


def test_diagnose_webcam_reports_unknown_failure_when_device_is_accessible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_profile.camera.diagnostics import diagnose_webcam

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(os.path, "exists", lambda _path: True)
    monkeypatch.setattr(os, "access", lambda _path, _mode: True)

    result = diagnose_webcam(1)

    assert result.device_path == "/dev/video1"
    assert result.device_exists is True
    assert result.permission_ok is True
    assert "held by another process" in result.hint
    assert "v4l2-ctl --device=/dev/video1" in result.hint


def test_user_in_video_group_returns_none_when_group_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grp

    from face_profile.camera.diagnostics import _user_in_video_group

    def _raise_key_error(_name: str) -> object:
        raise KeyError("video")

    monkeypatch.setattr(grp, "getgrnam", _raise_key_error)

    assert _user_in_video_group() is None


def test_user_in_video_group_true_when_gid_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grp

    from face_profile.camera.diagnostics import _user_in_video_group

    class _FakeGroupEntry:
        gr_gid = 44

    monkeypatch.setattr(grp, "getgrnam", lambda _name: _FakeGroupEntry())
    monkeypatch.setattr(os, "getgroups", lambda: [0, 44, 1000])

    assert _user_in_video_group() is True


def test_diagnose_webcam_real_filesystem_check_for_unlikely_device_index() -> None:
    """No mocking: confirm the real, unmocked code path degrades gracefully.

    Uses an implausibly high device index so this test behaves identically
    on a developer laptop and on Jetson/CI: the device is expected not to
    exist either way.
    """

    from face_profile.camera.diagnostics import diagnose_webcam

    result = diagnose_webcam(9999)

    if result.device_path is None:
        # Non-Linux CI/dev machine.
        return
    assert result.device_path == "/dev/video9999"
    assert result.device_exists is False
    assert "does not exist" in result.hint
