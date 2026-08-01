"""Hardened SQLite connection lifecycle for the M6 profile database."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

from face_profile.database.schema import apply_migrations


class DatabaseUnavailableError(RuntimeError):
    """Raised when the profile database cannot be opened safely."""


def open_database(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database file with owner-only permissions.

    WAL mode is enabled so readers are not blocked by writers; the ``-wal``
    and ``-shm`` sidecar files inherit the same directory permissions and,
    per ``database/crypto.py``, never contain plaintext biometric fields
    because encryption happens before values reach SQLite.

    ``check_same_thread=False`` is required from M12 onward: the headless
    daemon's background pipeline worker (``api/worker.py``) and its ASGI
    request-handling thread(s) both use this one connection. This is safe
    only because the underlying SQLite library reports
    ``sqlite3.threadsafety == 3`` (fully serialized mode, verified at
    daemon startup below) — SQLite itself serializes concurrent access
    rather than Python's sqlite3 module trusting single-thread usage.
    Dedicated per-thread connections or a queued writer are a documented
    follow-up for M15 rather than assumed safe by default here.
    """

    if sqlite3.threadsafety != 3:
        raise DatabaseUnavailableError(
            "sqlite3 module is not built in fully serialized threading mode; "
            "safe cross-thread access cannot be guaranteed"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as error:
        raise DatabaseUnavailableError("database directory unavailable") from error
    is_new = not path.exists()
    try:
        connection = sqlite3.connect(str(path), check_same_thread=False)
    except sqlite3.Error as error:
        raise DatabaseUnavailableError("database file unavailable") from error
    if is_new:
        _harden_permissions(path)
    else:
        _verify_permissions(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        apply_migrations(connection)
    except sqlite3.Error as error:
        connection.close()
        raise DatabaseUnavailableError("database initialization failed") from error
    return connection


def _harden_permissions(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as error:
        raise DatabaseUnavailableError("database file permissions could not be set") from error


def _verify_permissions(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError as error:
        raise DatabaseUnavailableError("database file unavailable") from error
    if stat.S_IMODE(mode) & 0o077:
        raise DatabaseUnavailableError("database file permissions are too permissive")
