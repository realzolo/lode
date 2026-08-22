"""Password hashing and signed session tokens (standard library only).

Passwords use PBKDF2-HMAC-SHA256 with a per-user random salt (no third-party
dependency). Session tokens are HMAC-SHA256 signed JSON web tokens (the same
structure as a JWT: ``header.payload.signature``) carrying ``sub``/``iat``/
``exp`` claims. The signature is verified on every request, so tokens cannot
be forged or tampered with.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

_ALG = "pbkdf2_sha256"
_ROUNDS = 100_000


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str, *, rounds: int = _ROUNDS) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{_ALG}${rounds}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        alg, rounds_s, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    if alg != _ALG:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        rounds = int(rounds_s)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(dk.hex(), dk_hex)


def create_token(sub: int, secret: str, ttl: int = 86400) -> str:
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    now = int(time.time())
    payload = _b64encode(json.dumps({"sub": sub, "iat": now, "exp": now + ttl}).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("utf-8")
    sig = _b64encode(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def decode_token(token: str, secret: str) -> dict[str, Any]:
    try:
        header, payload, sig = token.split(".")
    except ValueError:
        raise ValueError("malformed token")
    signing_input = f"{header}.{payload}".encode("utf-8")
    expected = _b64encode(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise ValueError("bad signature")
    try:
        data = json.loads(_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        raise ValueError("malformed payload")
    if data.get("exp", 0) < int(time.time()):
        raise ValueError("expired token")
    return data
