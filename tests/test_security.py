"""Tests for password hashing and signed-token helpers (standard library only).

These are pure unit tests with no database or network access.
"""

from __future__ import annotations

import time

from incident_trace.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

SECRET = "test-secret-key"


def test_hash_password_is_salted_and_deterministic_format():
    h1 = hash_password("s3cret")
    h2 = hash_password("s3cret")
    # Same password, different random salt -> different stored hashes.
    assert h1 != h2
    assert h1.count("$") == 3
    assert h1.startswith("pbkdf2_sha256$")


def test_verify_password_roundtrip():
    stored = hash_password("s3cret")
    assert verify_password("s3cret", stored) is True
    assert verify_password("wrong", stored) is False


def test_verify_password_rejects_malformed():
    assert verify_password("x", "not-a-valid-hash") is False
    assert verify_password("x", "") is False


def test_token_roundtrip_and_claims():
    token = create_token(42, SECRET, ttl=900)
    claims = decode_token(token, SECRET)
    assert claims["sub"] == 42
    assert claims["iat"] <= time.time() <= claims["exp"]


def test_decode_token_rejects_tampered_signature():
    token = create_token(42, SECRET, ttl=900)
    header, payload, _sig = token.split(".")
    forged = f"{header}.{payload}.ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    try:
        decode_token(forged, SECRET)
    except ValueError as exc:
        assert "bad signature" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for tampered signature")


def test_decode_token_rejects_malformed():
    # "a.b" splits into two parts (a JWT needs three) -> malformed token.
    try:
        decode_token("a.b", SECRET)
    except ValueError as exc:
        assert "malformed token" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for malformed token")


def test_decode_token_rejects_wrong_secret():
    token = create_token(42, SECRET, ttl=900)
    try:
        decode_token(token, "another-secret")
    except ValueError as exc:
        assert "bad signature" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for wrong secret")


def test_expired_token_is_rejected():
    token = create_token(42, SECRET, ttl=-10)  # already expired
    try:
        decode_token(token, SECRET)
    except ValueError as exc:
        assert "expired token" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for expired token")
