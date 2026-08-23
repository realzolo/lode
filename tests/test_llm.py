"""Unit tests for the LLM key resolution helper."""

from __future__ import annotations

import os

from lode.crypto import CryptoError, decrypt_secret, encrypt_secret
from lode.engine.llm import resolve_api_key


def test_resolve_api_key_env(monkeypatch):
    monkeypatch.setenv("LODE_TEST_LLM_KEY", "secret-from-env")
    assert resolve_api_key("env://LODE_TEST_LLM_KEY") == "secret-from-env"


def test_resolve_api_key_env_missing_returns_empty():
    os.environ.pop("LODE_TEST_LLM_MISSING", None)
    assert resolve_api_key("env://LODE_TEST_LLM_MISSING") == ""


def test_resolve_api_key_decrypts_encrypted_literal():
    # Literal keys are stored encrypted at rest; the resolver decrypts them.
    token = encrypt_secret("sk-super-secret")
    assert token != "sk-super-secret"
    assert resolve_api_key(token) == "sk-super-secret"
    assert decrypt_secret(token) == "sk-super-secret"


def test_resolve_api_key_plaintext_literal_raises():
    # No plaintext fallback: a value that is not a Fernet token (and not an
    # env:// reference) must fail closed rather than silently returning raw.
    with __import__("pytest").raises(CryptoError):
        resolve_api_key("sk-plaintext-not-encrypted")
