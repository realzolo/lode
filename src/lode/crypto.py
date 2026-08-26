"""Reversible encryption for all user-managed secrets stored by Lode."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from lode.config import settings

# Reused across calls; key derivation is cheap but caching avoids recomputing the
# SHA-256 + base64 on every encrypt/decrypt.
_fernet: Fernet | None = None


class CryptoError(Exception):
    """Raised when a value cannot be encrypted or decrypted."""


def _resolve_data_encryption_key() -> bytes:
    """Derive a Fernet key from the dedicated runtime setting."""
    if not settings.data_encryption_key:
        raise CryptoError("LODE_DATA_ENCRYPTION_KEY is required for secret operations")
    digest = hashlib.sha256(settings.data_encryption_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        # Fernet expects a 32-byte url-safe base64 key. SHA-256 of the resolved
        # data-encryption key material yields exactly 32 bytes; re-encoding makes
        # it url-safe base64.
        _fernet = Fernet(_resolve_data_encryption_key())
    return _fernet


def encrypt_secret(plaintext: str | None) -> str | None:
    """Encrypt a secret for storage.

    ``None`` / empty input returns ``None`` so we never store a ciphertext for
    an absent password (keeps the ``has_password`` flag honest).
    """
    if not plaintext:
        return None
    try:
        return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise CryptoError(f"failed to encrypt secret: {exc}") from exc


def decrypt_secret(ciphertext: str | None) -> str | None:
    """Decrypt a stored secret back to plaintext.

    ``None`` / empty input returns ``None``. A malformed or key-mismatched value
    raises :class:`CryptoError` (e.g. after a ``secret_key`` rotation).
    """
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError(
            "could not decrypt stored secret (data encryption key may have changed)"
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise CryptoError(f"failed to decrypt secret: {exc}") from exc
