"""Unit tests for the LLM key resolution helper."""

from __future__ import annotations

import os
import asyncio

from lode.crypto import CryptoError, decrypt_secret, encrypt_secret
from lode.engine.llm import _usage, complete_with_usage, resolve_api_key


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


def test_usage_records_provider_exact_or_explicit_local_estimate():
    exact = _usage("openai", {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}, "system", "user", "answer")
    estimated = _usage("openai", {}, "system", "user", "answer")
    assert exact == (12, 8, 20, "provider")
    assert estimated[3] == "estimated"
    assert estimated[2] == estimated[0] + estimated[1]


def test_unconfigured_model_is_auditable_fallback_without_token_claims():
    result = asyncio.run(complete_with_usage("system", "user", None))
    assert result.text is None
    assert result.error_code == "model_not_configured"
    assert result.token_source == "unavailable"
    assert result.total_tokens is None
