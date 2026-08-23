"""Reversible encryption for at-rest secrets (data-source passwords).

Data-source credentials are stored on the ``db_sources`` row so the admin UI
can supply a connection directly. To avoid keeping plaintext passwords in the
database we encrypt them with Fernet (AES-128-CBC + HMAC-SHA256) using a key
derived from ``settings.secret_key``.

The same ``secret_key`` is used for JWT signing, so no new environment variable
is required. Rotating ``secret_key`` will invalidate already-encrypted
passwords — admins simply re-enter them. There is no plaintext fallback: if a
value cannot be decrypted it is treated as a config error (see ``CryptoError``).
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from lode.config import settings

# Reused across calls; key derivation is cheap but caching avoids recomputing the
# SHA-256 + base64 on every encrypt/decrypt.
_fernet: Fernet | None = None


class CryptoError(Exception):
    """Raised when a value cannot be encrypted or decrypted."""


def _resolve_data_encryption_key() -> bytes:
    """Resolve the Fernet key used to encrypt at-rest data-source secrets.

    Separation of duties: the auth signing key (``settings.secret_key``) MUST NOT
    also encrypt data, so a JWT-signing-key leak cannot decrypt stored
    credentials. The dedicated key is supplied via ``data_encryption_key_ref``,
    which is strictly an ``env://NAME`` reference (never a plaintext literal) —
    the referenced value is derived into a 32-byte url-safe base64 Fernet key.

    When ``data_encryption_key_ref`` is empty we fall back to deriving the key
    from ``secret_key``. This preserves the ability to decrypt data already
    encrypted under the legacy single-key scheme; it is the documented default,
    not a compatibility shim layered on top of the new path.
    """
    ref = settings.data_encryption_key_ref
    if ref:
        if not ref.startswith("env://"):
            raise CryptoError(
                "DATA_ENCRYPTION_KEY_REF must be an env:// reference, never a "
                "plaintext literal"
            )
        name = ref[len("env://") :]
        value = os.environ.get(name)
        if not value:
            raise CryptoError(f"environment variable '{name}' is not set")
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
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
            "could not decrypt data source secret (secret_key may have changed)"
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise CryptoError(f"failed to decrypt secret: {exc}") from exc
