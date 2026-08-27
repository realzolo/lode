"""Short-lived HMAC capabilities bound to one durable authorized read."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any, Mapping


class AuthorizationTokenError(ValueError):
    pass


def issue_token(claims: Mapping[str, Any], *, key: str) -> str:
    if not key:
        raise AuthorizationTokenError("evidence authorization key is required")
    payload = dict(claims)
    payload["nonce"] = secrets.token_urlsafe(24)
    encoded = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = _encode(hmac.new(key.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_token(token: str, *, key: str, now: datetime | None = None) -> dict[str, Any]:
    if not key:
        raise AuthorizationTokenError("evidence authorization key is required")
    try:
        encoded, signature = token.split(".")
    except ValueError as exc:
        raise AuthorizationTokenError("malformed authorization token") from exc
    expected = _encode(hmac.new(key.encode(), encoded.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise AuthorizationTokenError("invalid authorization signature")
    try:
        claims = json.loads(_decode(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthorizationTokenError("invalid authorization claims") from exc
    if not isinstance(claims, dict):
        raise AuthorizationTokenError("invalid authorization claims")
    required = {
        "investigation_id", "candidate_hash", "decision_hash", "snapshot_hash",
        "policy_hash", "effective_action_hash", "expires_at", "nonce",
    }
    if set(claims) != required:
        raise AuthorizationTokenError("authorization claim set mismatch")
    current = now or datetime.now(UTC)
    try:
        expires_at = datetime.fromisoformat(str(claims["expires_at"]))
    except ValueError as exc:
        raise AuthorizationTokenError("invalid authorization expiry") from exc
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise AuthorizationTokenError("authorization expiry has no timezone")
    if current >= expires_at:
        raise AuthorizationTokenError("authorization token expired")
    return claims


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
