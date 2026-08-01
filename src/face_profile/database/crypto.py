"""AES-256-GCM field-level encryption for sensitive database columns.

M6 encrypts biometric payloads (embedding vectors, and any future retained
image bytes) at the field level rather than encrypting the whole SQLite
file. Because SQLite only ever writes the ciphertext bytes we hand it to
disk, WAL and rollback-journal sidecar files never contain plaintext
biometric values either, without requiring a page-level encryption
extension such as SQLCipher.
"""

from __future__ import annotations

import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12


class EncryptionError(RuntimeError):
    """Raised when a sensitive field cannot be encrypted or decrypted safely."""


def encrypt_bytes(key: bytes, plaintext: bytes, *, associated_data: bytes = b"") -> bytes:
    """Encrypt with a fresh random nonce, returning ``nonce || ciphertext``."""

    nonce = secrets.token_bytes(_NONCE_LENGTH)
    try:
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    except Exception as error:
        raise EncryptionError("field encryption failed") from error
    return nonce + ciphertext


def decrypt_bytes(key: bytes, blob: bytes, *, associated_data: bytes = b"") -> bytes:
    """Decrypt a ``nonce || ciphertext`` blob produced by :func:`encrypt_bytes`."""

    if len(blob) <= _NONCE_LENGTH:
        raise EncryptionError("ciphertext is truncated")
    nonce, ciphertext = blob[:_NONCE_LENGTH], blob[_NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, associated_data)
    except InvalidTag as error:
        raise EncryptionError("field decryption failed: authentication check failed") from error
    except Exception as error:
        raise EncryptionError("field decryption failed") from error
