"""Local encryption-key storage, separated from the encrypted database file.

This is a minimal M6 key provider: a 32-byte AES-256-GCM key stored in its
own owner-only file, outside the database directory by configuration
convention. It satisfies ADR 0003's key/data separation requirement but is
not platform key-management (HSM/KMS) integration; rotation and disaster
recovery remain a documented manual procedure until a production key-
management adapter is selected.

M17 routes both key creation and load-time verification through
``face_profile.platform_security``: POSIX keeps its existing ``0700``/
``0600`` enforcement, and Windows gets an equivalent protected-DACL
boundary (current user + LocalSystem only) instead of the no-op that
``os.chmod``/``mkdir(mode=...)`` are on that platform.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Protocol

from face_profile.platform_security import (
    SecurityBoundaryError,
    ensure_private_directory,
    harden_new_file,
    posix_open_flags,
    verify_private_file,
)


class KeyUnavailableError(RuntimeError):
    """Raised when the database encryption key cannot be loaded or created."""


class KeyProvider(Protocol):
    """Supplies the symmetric key used for field-level encryption."""

    def get_key(self) -> bytes: ...


class LocalFileKeyProvider:
    """Load an existing key file or create one with owner-only permissions."""

    KEY_LENGTH = 32

    def __init__(self, path: Path) -> None:
        self._path = path

    def get_key(self) -> bytes:
        """Return the 32-byte key, generating and persisting it on first use."""

        if self._path.exists():
            return self._load_existing()
        return self._generate()

    def _load_existing(self) -> bytes:
        try:
            verify_private_file(self._path)
        except SecurityBoundaryError as error:
            raise KeyUnavailableError(
                "encryption key file permissions are too permissive"
            ) from error
        try:
            key = self._path.read_bytes()
        except OSError as error:
            raise KeyUnavailableError("encryption key unavailable") from error
        if len(key) != self.KEY_LENGTH:
            raise KeyUnavailableError("encryption key has an unexpected length")
        return key

    def _generate(self) -> bytes:
        key = secrets.token_bytes(self.KEY_LENGTH)
        try:
            ensure_private_directory(self._path.parent)
        except SecurityBoundaryError as error:
            raise KeyUnavailableError("encryption key directory unavailable") from error
        flags = posix_open_flags(truncate=False, exclusive=True)
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except OSError as error:
            raise KeyUnavailableError("encryption key could not be created") from error
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key)
        except OSError as error:
            raise KeyUnavailableError("encryption key could not be created") from error
        try:
            harden_new_file(self._path)
        except SecurityBoundaryError as error:
            raise KeyUnavailableError("encryption key could not be secured") from error
        return key
