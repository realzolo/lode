"""Unit tests for the LLM key resolution helper."""

from __future__ import annotations

import asyncio
import json

from lode.crypto import CryptoError, decrypt_secret, encrypt_secret
from lode.engine.llm import (
    ModelConfig,
    ResponseSchema,
    _usage,
    complete_with_usage,
    model_endpoint,
    resolve_api_key,
)
from lode.evidence_connectors.types import ProviderExecutionError, ProviderHTTPResponse


def test_resolve_api_key_decrypts_encrypted_literal():
    # Literal keys are stored encrypted at rest; the resolver decrypts them.
    token = encrypt_secret("sk-super-secret")
    assert token != "sk-super-secret"
    assert resolve_api_key(token) == "sk-super-secret"
    assert decrypt_secret(token) == "sk-super-secret"


def test_resolve_api_key_plaintext_literal_raises():
    # No plaintext or indirect-reference fallback is accepted.
    with __import__("pytest").raises(CryptoError):
        resolve_api_key("sk-plaintext-not-encrypted")


def test_usage_records_provider_exact_or_explicit_local_estimate():
    exact = _usage(
        {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}},
        "system",
        "user",
        "answer",
    )
    estimated = _usage({}, "system", "user", "answer")
    assert exact == (12, 8, 20, "provider")
    assert estimated[3] == "estimated"
    assert estimated[2] == estimated[0] + estimated[1]


def test_unconfigured_model_is_auditable_fallback_without_token_claims():
    result = asyncio.run(complete_with_usage("system", "user", None))
    assert result.text is None
    assert result.error_code == "model_not_configured"
    assert result.token_source == "unavailable"
    assert result.total_tokens is None
    assert result.attempt_count == 0


def test_model_endpoint_normalizes_provider_base_urls():
    assert (
        model_endpoint("openai.chat_completions.v1", "https://model.example")
        == "https://model.example/v1/chat/completions"
    )
    assert (
        model_endpoint("openai.responses.v1", "https://model.example/v1")
        == "https://model.example/v1/responses"
    )
    assert (
        model_endpoint("anthropic.messages.v1", "https://model.example")
        == "https://model.example/v1/messages"
    )


class _Response(ProviderHTTPResponse):
    def __init__(self, payload: dict | str, content_type: str = "application/json") -> None:
        raw = json.dumps(payload) if isinstance(payload, dict) else payload
        super().__init__(200, {"content-type": content_type}, raw.encode())


def _config() -> ModelConfig:
    return ModelConfig(
        protocol_id="openai.chat_completions.v1",
        base_url="https://model.example",
        api_key_ciphertext=encrypt_secret("test-key"),
        model="test-model",
    )


def test_transient_provider_failures_retry_and_record_attempt_count(monkeypatch):
    attempts: list[str] = []

    async def request(_method, endpoint, **_kwargs):
        attempts.append(endpoint)
        if len(attempts) < 3:
            return ProviderHTTPResponse(503, {}, b"")
        return _Response({"content": [{"type": "text", "text": "OK"}]})

    monkeypatch.setattr("lode.engine.llm.provider_request", request)
    monkeypatch.setattr("lode.engine.llm.LLM_RETRY_BASE_DELAY_SECONDS", 0)
    result = asyncio.run(complete_with_usage("system", "user", _config()))
    assert result.text == "OK"
    assert result.attempt_count == 3
    assert attempts == ["https://model.example/v1/chat/completions"] * 3


def test_non_json_provider_response_fails_without_pointless_retries(monkeypatch):
    attempts = 0

    async def request(_method, _endpoint, **kwargs):
        nonlocal attempts
        attempts += 1
        assert kwargs["timeout_seconds"] == 120
        return _Response("<html>not an API</html>", "text/html")

    monkeypatch.setattr("lode.engine.llm.provider_request", request)
    result = asyncio.run(complete_with_usage("system", "user", _config()))
    assert result.text is None
    assert result.error_code == "invalid_response"
    assert result.attempt_count == 1
    assert attempts == 1


def test_invalid_provider_response_fails_without_retry_and_keeps_stable_code(monkeypatch):
    attempts = 0

    async def request(_method, _endpoint, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise ProviderExecutionError("invalid_response", "test provider response")

    monkeypatch.setattr("lode.engine.llm.provider_request", request)

    result = asyncio.run(complete_with_usage("system", "user", _config()))

    assert result.text is None
    assert result.error_code == "invalid_response"
    assert result.attempt_count == 1
    assert attempts == 1


def test_non_object_provider_json_is_an_invalid_response(monkeypatch):
    async def request(_method, _endpoint, **_kwargs):
        return _Response("[]")

    monkeypatch.setattr("lode.engine.llm.provider_request", request)

    result = asyncio.run(complete_with_usage("system", "user", _config()))

    assert result.error_code == "invalid_response"


def test_structured_request_sets_json_mode_routed_output_limit_and_custom_timeout(monkeypatch):
    captured: dict = {}

    async def request(_method, _endpoint, **kwargs):
        captured.update(kwargs["json_body"])
        captured["timeout"] = kwargs["timeout_seconds"]
        return _Response({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr("lode.engine.llm.provider_request", request)
    config = ModelConfig(
        protocol_id="openai.chat_completions.v1",
        base_url="https://model.example",
        api_key_ciphertext=encrypt_secret("test-key"),
        model="test-model",
        max_completion_tokens=321,
    )
    result = asyncio.run(
        complete_with_usage(
            "Return JSON.",
            "{}",
            config,
            json_mode=True,
            timeout_seconds=45,
        )
    )
    assert result.text == "{}"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["max_completion_tokens"] == 321
    assert captured["timeout"] == 45


def test_strict_response_schema_replaces_loose_json_mode(monkeypatch):
    captured: dict = {}
    schema = ResponseSchema(
        name="strict_result",
        schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        },
    )

    async def request(_method, _endpoint, **kwargs):
        assert kwargs["timeout_seconds"] == 120
        captured.update(kwargs["json_body"])
        return _Response({"choices": [{"message": {"content": '{"status":"ok"}'}}]})

    monkeypatch.setattr("lode.engine.llm.provider_request", request)
    result = asyncio.run(
        complete_with_usage("Return JSON.", "{}", _config(), json_mode=True, response_schema=schema)
    )

    assert result.text == '{"status":"ok"}'
    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "strict_result", "strict": True, "schema": schema.schema},
    }


def test_anthropic_uses_its_native_authentication_headers(monkeypatch):
    captured: dict = {}

    async def request(_method, _endpoint, **kwargs):
        captured.update(kwargs["headers"])
        return _Response({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr("lode.engine.llm.provider_request", request)
    config = ModelConfig(
        protocol_id="anthropic.messages.v1",
        base_url="https://model.example",
        api_key_ciphertext=encrypt_secret("test-key"),
        model="test-model",
    )
    assert asyncio.run(complete_with_usage("system", "user", config)).text == "OK"
    assert captured["x-api-key"] == "test-key"
    assert captured["anthropic-version"]
