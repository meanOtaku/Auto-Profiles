"""Cross-platform owner-only security boundary for sensitive local files.

Every sensitive artifact this project writes outside version control --
the M6 database-encryption key (``database/keys.py``), the SQLite profile
database and its WAL/SHM sidecars (``database/connection.py``), and
explicit debug-frame output (``camera/__init__.py``'s ``save_frame``) --
must be readable only by the account running the service. POSIX satisfies
this with ``0700``/``0600`` mode bits, already implemented and verified
(never merely set-and-trust) before M17. Windows has no POSIX mode bits:
NTFS access control is a discretionary access control list (DACL), so M17
adds an equivalent boundary here rather than silently doing nothing on
Windows, which is what the pre-M17 ``os.chmod`` / ``mkdir(mode=...)``
calls actually did there (Windows ignores POSIX mode bits for anything
beyond the read-only attribute).

This module is imported unconditionally by Linux-only code paths (the
existing test suite, ``database/keys.py``, etc.), so nothing at module
import time may require a Windows-only package. All ``pywin32`` access is
isolated in :mod:`face_profile._win_security` and imported lazily, only
from inside the ``platform.system() == "Windows"`` branches below.
"""

from __future__ import annotations

import os
import platform
import stat
from pathlib import Path

_IS_WINDOWS = platform.system() == "Windows"


class SecurityBoundaryError(RuntimeError):
    """Raised when a sensitive path cannot be created or verified as private."""


def reject_unsafe_target(path: Path) -> None:
    """Raise if an existing ``path`` is a symlink or (Windows) reparse point.

    This is a best-effort pre-check, not an atomic guarantee: POSIX callers
    additionally pass ``O_NOFOLLOW`` on their own ``os.open()`` call, which
    *is* atomic and is the real enforcement there. Windows has no equivalent
    flag available through Python's stdlib ``os.open()``, so on Windows this
    pre-check (plus a post-creation re-check performed by callers where
    practical) is the actual boundary and carries a documented TOCTOU
    limitation, consistent with this project's practice of recording real
    limitations rather than claiming an unverified guarantee.
    """

    try:
        info = path.lstat()
    except FileNotFoundError:
        return  # Does not exist yet; nothing to reject.
    except OSError as error:
        raise SecurityBoundaryError(f"could not inspect sensitive path: {path}") from error
    if stat.S_ISLNK(info.st_mode):
        raise SecurityBoundaryError(f"refusing to use a symlinked path: {path}")
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise SecurityBoundaryError(f"refusing to use a reparse-point path: {path}")


def ensure_private_directory(path: Path) -> None:
    """Create ``path`` (with parents) as owner-only.

    Sidecar files SQLite creates next to the database (``-wal``/``-shm``)
    are never explicitly chmod'd by this project's own code -- they must
    inherit safety from their parent directory. On POSIX, ``0700`` denies
    all non-owner directory access outright; matching pre-M17 behavior,
    a directory that already exists is left as-is (``mkdir``'s ``mode``
    only takes effect on creation, and this function never silently
    re-tightens a directory a user or an earlier version created).

    Windows had no equivalent boundary at all before M17 (``mkdir(mode=)``
    is a POSIX-only no-op there), so there is no pre-existing behavior to
    preserve: a freshly created directory gets a protected DACL built with
    inheritable ACEs (current user + ``LocalSystem``, full control) so
    files SQLite creates underneath inherit the same restricted access,
    and an *existing* directory is verified rather than trusted -- failing
    closed with an actionable error is judged safer than silently
    continuing against a directory this project cannot confirm is private.
    """

    reject_unsafe_target(path)
    if path.is_dir():
        if _IS_WINDOWS:
            verify_private_directory(path)
        return
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _IS_WINDOWS:
        from face_profile import _win_security

        _win_security.apply_protected_dacl(path, directory=True)
        verify_private_directory(path)


def verify_private_directory(path: Path) -> None:
    """Raise :class:`SecurityBoundaryError` unless ``path`` is owner-only."""

    reject_unsafe_target(path)
    if _IS_WINDOWS:
        from face_profile import _win_security

        _win_security.verify_protected_dacl(path, directory=True)
        return
    try:
        mode = path.stat().st_mode
    except OSError as error:
        raise SecurityBoundaryError(f"directory unavailable: {path}") from error
    if stat.S_IMODE(mode) & 0o077:
        raise SecurityBoundaryError(f"directory permissions are too permissive: {path}")


def harden_new_file(path: Path) -> None:
    """Apply owner-only protection to a file this process just created.

    POSIX: ``chmod(0o600)``, whether or not the caller already created the
    file with that mode via ``os.open()`` (idempotent either way; needed
    for callers such as ``sqlite3.connect()`` that create the file through
    a library that does not accept an explicit creation mode). Windows:
    sets an explicit, non-inherited (protected) DACL granting only the
    current user and ``LocalSystem`` full control.
    """

    reject_unsafe_target(path)
    if _IS_WINDOWS:
        from face_profile import _win_security

        _win_security.apply_protected_dacl(path, directory=False)
    else:
        try:
            path.chmod(0o600)
        except OSError as error:
            raise SecurityBoundaryError(f"could not set owner-only permissions: {path}") from error
    verify_private_file(path)


def verify_private_file(path: Path) -> None:
    """Raise :class:`SecurityBoundaryError` unless ``path`` is owner-only."""

    reject_unsafe_target(path)
    if _IS_WINDOWS:
        from face_profile import _win_security

        _win_security.verify_protected_dacl(path, directory=False)
        return
    try:
        mode = path.stat().st_mode
    except OSError as error:
        raise SecurityBoundaryError(f"file unavailable: {path}") from error
    if stat.S_IMODE(mode) & 0o077:
        raise SecurityBoundaryError(f"file permissions are too permissive: {path}")


def posix_open_flags(*, truncate: bool, exclusive: bool) -> int:
    """Return the ``os.open`` flags this project's writers standardize on.

    Always includes ``O_NOFOLLOW`` where the platform defines it (every
    POSIX target; absent on Windows, where :func:`reject_unsafe_target`
    plus :func:`harden_new_file`'s post-creation verification are the
    boundary instead).
    """

    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if truncate else 0
    flags |= os.O_EXCL if exclusive else 0
    return flags
