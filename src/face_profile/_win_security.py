"""Windows-only NTFS DACL implementation backing ``platform_security``.

Never imported at module scope from any Linux-reachable code path -- only
``face_profile.platform_security`` imports this, and only from inside a
runtime ``platform.system() == "Windows"`` branch. Requires the optional,
Windows-only ``pywin32`` dependency (``sys_platform == 'win32'`` marker in
``pyproject.toml``), which is never installed on Linux/macOS, so this
module's own imports are only ever exercised on Windows.

**Static verification only.** No Windows machine was available while
writing this module. The DACL construction and verification logic below
follows documented ``pywin32``/Win32 security APIs and was checked with
Ruff/mypy, but has not been exercised against a real NTFS volume. See
``docs/reports/M17.md`` for exactly what was and was not verified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ntsecuritycon  # type: ignore[import-untyped]
import win32api  # type: ignore[import-untyped]
import win32con  # type: ignore[import-untyped]
import win32security  # type: ignore[import-untyped]

from face_profile.platform_security import SecurityBoundaryError

_INHERIT_TO_CHILDREN = win32security.CONTAINER_INHERIT_ACE | win32security.OBJECT_INHERIT_ACE
_NO_INHERITANCE = 0
_SECURITY_INFO = (
    win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION
)


def _current_user_sid() -> Any:
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    user_sid, _attributes = win32security.GetTokenInformation(token, win32security.TokenUser)
    return user_sid


def _local_system_sid() -> Any:
    return win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)


def _allowed_sids() -> tuple[Any, Any]:
    return (_current_user_sid(), _local_system_sid())


def apply_protected_dacl(path: Path, *, directory: bool) -> None:
    """Set an explicit DACL: current user + LocalSystem, full control only.

    ``PROTECTED_DACL_SECURITY_INFORMATION`` disables inheritance *from the
    parent* so no broader ACE the parent directory might carry leaks in.
    For directories, the two ACEs this function adds are themselves marked
    inheritable (``CONTAINER_INHERIT_ACE | OBJECT_INHERIT_ACE``) so files
    SQLite creates underneath (``-wal``/``-shm`` sidecars) automatically
    inherit the same restricted access without this project explicitly
    hardening each sidecar file.
    """

    try:
        user_sid = _current_user_sid()
        system_sid = _local_system_sid()
        dacl = win32security.ACL()
        inherit_flags = _INHERIT_TO_CHILDREN if directory else _NO_INHERITANCE
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            inherit_flags,
            ntsecuritycon.FILE_ALL_ACCESS,
            user_sid,
        )
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            inherit_flags,
            ntsecuritycon.FILE_ALL_ACCESS,
            system_sid,
        )
        security_descriptor = win32security.SECURITY_DESCRIPTOR()
        security_descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(str(path), _SECURITY_INFO, security_descriptor)
    except Exception as error:
        raise SecurityBoundaryError(f"could not set owner-only DACL on {path}") from error


def verify_protected_dacl(path: Path, *, directory: bool) -> None:
    """Raise unless ``path`` has the exact protected private-access boundary.

    A missing (null) DACL means "everyone" has access and is rejected. The
    DACL must be protected from parent inheritance, grant full control to
    exactly the current user and LocalSystem, and, for directories, carry
    inheritable ACEs so SQLite sidecars receive the same boundary. Deny
    entries are ignored because they can only narrow effective access.
    """

    try:
        security_descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        dacl = security_descriptor.GetSecurityDescriptorDacl()
        allowed = {str(sid) for sid in _allowed_sids()}
    except Exception as error:
        raise SecurityBoundaryError(f"could not read DACL for {path}") from error
    if dacl is None:
        raise SecurityBoundaryError(f"{path} has no DACL (unrestricted access)")
    control, _revision = security_descriptor.GetSecurityDescriptorControl()
    if not control & win32security.SE_DACL_PROTECTED:
        raise SecurityBoundaryError(f"{path} inherits an unprotected DACL")

    observed: set[str] = set()
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        (ace_type, ace_flags), mask, trustee_sid = ace
        if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
            continue
        trustee = str(trustee_sid)
        if trustee not in allowed:
            raise SecurityBoundaryError(
                f"{path} grants access to an unexpected principal; refusing to trust it"
            )
        if mask & ntsecuritycon.FILE_ALL_ACCESS != ntsecuritycon.FILE_ALL_ACCESS:
            raise SecurityBoundaryError(f"{path} does not grant the required private access")
        if directory and ace_flags & _INHERIT_TO_CHILDREN != _INHERIT_TO_CHILDREN:
            raise SecurityBoundaryError(f"{path} DACL does not protect child files")
        observed.add(trustee)
    if observed != allowed:
        raise SecurityBoundaryError(f"{path} DACL is missing a required private principal")
