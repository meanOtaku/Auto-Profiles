"""Best-effort, hardware-free webcam availability diagnostics.

Used only to enrich actionable error text when a webcam source fails to
open (e.g. a missing ``/dev/videoN`` node or a permission gap — the most
common causes on a freshly-imaged, possibly headless Linux box such as a
Jetson running JetPack Ubuntu). This module never opens the camera itself
and never changes control flow: it is pure inspection of the filesystem
and process identity, safe to call even when no camera is present.
"""

import os
import platform
from dataclasses import dataclass

_VIDEO_GROUP_NAME = "video"


@dataclass(frozen=True, slots=True)
class WebcamDiagnostics:
    """A best-effort explanation for why a webcam device could not be opened."""

    device_path: str | None
    device_exists: bool | None
    permission_ok: bool | None
    hint: str


def _user_in_video_group() -> bool | None:
    """Return whether the current process belongs to the 'video' group.

    Returns ``None`` when this cannot be determined (non-POSIX platform, or
    no 'video' group exists on this system) rather than guessing.
    """

    try:
        import grp
    except ImportError:
        return None
    try:
        video_group = grp.getgrnam(_VIDEO_GROUP_NAME)
    except KeyError:
        return None
    try:
        group_ids = os.getgroups()
    except OSError:
        return None
    return video_group.gr_gid in group_ids


def diagnose_webcam(device_index: int) -> WebcamDiagnostics:
    """Inspect the local system for a likely reason a webcam failed to open.

    Linux-only (``/dev/video*`` + V4L2 device nodes); on other platforms
    this returns a generic hint since device enumeration is not portable.
    """

    if platform.system() != "Linux":
        return WebcamDiagnostics(
            device_path=None,
            device_exists=None,
            permission_ok=None,
            hint=(
                "webcam diagnostics beyond this message are Linux-only; confirm the "
                "camera is connected, its driver is installed, and no other "
                "application is holding it open"
            ),
        )

    device_path = f"/dev/video{device_index}"
    device_exists = os.path.exists(device_path)
    if not device_exists:
        return WebcamDiagnostics(
            device_path=device_path,
            device_exists=False,
            permission_ok=None,
            hint=(
                f"{device_path} does not exist, so no camera is enumerated at "
                f"device_index {device_index}. Run `v4l2-ctl --list-devices` "
                "(install the 'v4l-utils' package if the command is missing) to "
                "see connected cameras and their indices, and check `dmesg | "
                "tail` for USB enumeration errors if the camera was just "
                "plugged in"
            ),
        )

    permission_ok = os.access(device_path, os.R_OK | os.W_OK)
    if not permission_ok:
        in_video_group = _user_in_video_group()
        group_hint = " this user is not in the 'video' group," if in_video_group is False else ""
        return WebcamDiagnostics(
            device_path=device_path,
            device_exists=True,
            permission_ok=False,
            hint=(
                f"{device_path} exists but is not readable/writable by this "
                f"process;{group_hint} add the user to it with `sudo usermod "
                "-aG video $USER` and start a new login session (log out/in, or "
                "reboot) for the group change to take effect, then re-run"
            ),
        )

    return WebcamDiagnostics(
        device_path=device_path,
        device_exists=True,
        permission_ok=True,
        hint=(
            f"{device_path} exists and is accessible, but OpenCV could not open "
            f"it; it may be held by another process (check `fuser {device_path}` "
            f"or `lsof {device_path}`), or the driver may need a moment after "
            f"being plugged in — verify with `v4l2-ctl --device={device_path} "
            "--all`"
        ),
    )
