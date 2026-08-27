"""Strict serialization for Git account credentials kept in encrypted revisions."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from lode.config import settings


@dataclass(frozen=True, slots=True)
class GitAccountSecret:
    username: str
    token: str


def encode_credential_secret(secret: GitAccountSecret) -> str:
    """Return the sole on-disk representation for an account read credential."""
    return json.dumps(
        {"token": secret.token, "username": secret.username},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_credential_secret(value: str) -> GitAccountSecret:
    """Decode a strict JSON object without accepting ambiguous duplicate keys."""
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_object)
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Git account credential is not valid strict JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"username", "token"}:
        raise ValueError("Git account credential has an invalid shape")
    username = parsed["username"]
    token = parsed["token"]
    if (
        not isinstance(username, str)
        or not username.strip()
        or not isinstance(token, str)
        or not token.strip()
    ):
        raise ValueError("Git account credential fields must be non-empty strings")
    return GitAccountSecret(username=username, token=token)


def credential_identity_hash(secret: GitAccountSecret) -> str:
    """Compute a non-reversible identity used by immutable investigation snapshots."""
    payload = f"token\0{secret.token}\0username\0{secret.username}".encode()
    return hmac.new(settings.credential_identity_key.encode(), payload, hashlib.sha256).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value
