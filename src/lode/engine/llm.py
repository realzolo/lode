"""Provider-neutral LLM gateway for audited analysis invocations.

The caller supplies the endpoint, model, and encrypted credential selected from
the investigation's frozen provider/deployment/binding snapshots.

If no model is configured, or the request fails for any reason, this module
returns an unavailable result. The investigation reports that semantic
attribution could not run instead of manufacturing a root cause.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from lode.crypto import decrypt_secret
from lode.evidence_connectors.types import ProviderExecutionError
from lode.infrastructure.provider_http import provider_request
from lode.metrics import LLM_CALLS, LLM_LATENCY
from lode.runtime_defaults import (
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LLM_RETRY_BASE_DELAY_SECONDS,
)

logger = logging.getLogger("lode.engine.llm")


@dataclass
class ModelConfig:
    provider: str
    base_url: str
    api_key_ciphertext: str
    model: str
    max_completion_tokens: int | None = None
    organization_ref: str | None = None
    project_ref: str | None = None


@dataclass(frozen=True)
class ResponseSchema:
    """Provider-neutral strict JSON output contract."""

    name: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class CompletionResult:
    """A model result with usage metadata safe to persist in investigation audit."""

    text: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    token_source: str
    error_code: str | None = None
    error_detail: str | None = None
    attempt_count: int = 0


RetryCallback = Callable[[int, int, str, float], Awaitable[None]]


class ProviderHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"provider returned HTTP {status_code}")


def model_endpoint(provider: str, base_url: str) -> str:
    """Resolve a configured API base URL to the provider's completion endpoint."""
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions" if path.endswith("/v1") else f"{path}/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def resolve_api_key(api_key_ciphertext: str) -> str:
    """Decrypt a model credential stored by the control plane."""
    return decrypt_secret(api_key_ciphertext) or ""


def _is_retryable(exc: Exception) -> bool:
    """Classify a failure as transient (worth retrying) or fatal.

    * Network errors (DNS, refused, reset) and timeouts are transient.
    * HTTP 5xx from the provider (overload, gateway) are transient.
    * HTTP 4xx are client errors (bad key, bad request) — retrying cannot fix
      them, so we fail closed immediately.
    """
    if isinstance(exc, ProviderHTTPError):
        return exc.status_code == 429 or 500 <= exc.status_code <= 599
    if isinstance(exc, ProviderExecutionError):
        return exc.code in {"provider_timeout", "provider_unavailable", "rate_limited"}
    return isinstance(exc, (TimeoutError, ConnectionError))


async def complete(
    system_prompt: str,
    user_prompt: str,
    config: ModelConfig | None,
) -> str | None:
    """Return the assistant message text, or ``None`` if unavailable.

    The call uses the bounded provider transport. Transient failures
    (network blips, provider 5xx) are retried with bounded
    exponential backoff; after exhausting retries the engine returns an
    unavailable result and does not synthesize a diagnosis.
    """
    return (await complete_with_usage(system_prompt, user_prompt, config)).text


def _estimated_tokens(value: str) -> int:
    """Estimate post-call usage only when a provider omits usage metadata.

    Admission and context assembly are performed before this gateway with the
    frozen deployment's registered exact tokenizer; this estimate never decides
    whether a request fits.
    """
    return max(1, (len(value) + 3) // 4)


def _usage(
    body: dict[str, Any], system_prompt: str, user_prompt: str, text: str
) -> tuple[int, int, int, str]:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total = usage.get("total_tokens")
        return (
            input_tokens,
            output_tokens,
            total if isinstance(total, int) else input_tokens + output_tokens,
            "provider",
        )
    input_estimate = _estimated_tokens(system_prompt + "\n" + user_prompt)
    output_estimate = _estimated_tokens(text)
    return input_estimate, output_estimate, input_estimate + output_estimate, "estimated"


async def complete_with_usage(
    system_prompt: str,
    user_prompt: str,
    config: ModelConfig | None,
    *,
    json_mode: bool = False,
    response_schema: ResponseSchema | None = None,
    timeout_seconds: float | None = None,
    on_retry: RetryCallback | None = None,
) -> CompletionResult:
    """Run one bounded completion and retain provider usage when available."""
    started = time.monotonic()
    if config is None or not config.api_key_ciphertext:
        return CompletionResult(
            None,
            0,
            None,
            None,
            None,
            "unavailable",
            "model_not_configured",
            "No model configuration was selected.",
            0,
        )

    api_key = resolve_api_key(config.api_key_ciphertext)
    if not api_key:
        return CompletionResult(
            None,
            int((time.monotonic() - started) * 1000),
            None,
            None,
            None,
            "unavailable",
            "api_key_unavailable",
            "The configured API key could not be decrypted.",
            0,
        )

    payload, headers = _openai_payload(
        api_key,
        config.base_url,
        config.model,
        system_prompt,
        user_prompt,
        max_completion_tokens=config.max_completion_tokens,
        organization_ref=config.organization_ref,
        project_ref=config.project_ref,
        json_mode=json_mode,
        response_schema=response_schema,
    )

    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"
    endpoint = model_endpoint(config.provider, config.base_url)
    request_timeout = max(1.0, timeout_seconds or LLM_REQUEST_TIMEOUT_SECONDS)

    async def _post() -> dict[str, Any]:
        response = await provider_request(
            "POST",
            endpoint,
            headers=headers,
            timeout_seconds=request_timeout,
            json_body=payload,
        )
        if response.status_code >= 400:
            raise ProviderHTTPError(response.status_code)
        raw = response.body.decode("utf-8")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            content_type = response.headers.get("content-type", "unknown")
            raise json.JSONDecodeError(
                f"provider returned non-JSON content ({content_type})",
                exc.doc,
                exc.pos,
            ) from exc
        if not isinstance(value, dict):
            raise TypeError("provider response must be a JSON object")
        return value

    max_retries = LLM_MAX_RETRIES
    base_delay = LLM_RETRY_BASE_DELAY_SECONDS
    last_exc: Exception | None = None
    # Time only the network round-trip(s); retries add their own sleep that is
    # not part of "provider latency". A successful call reports one observation.
    for attempt in range(1, max_retries + 1):
        try:
            body = await _post()
            text = _extract_text(body, response_schema=response_schema)
            elapsed = time.monotonic() - started
            input_tokens, output_tokens, total_tokens, token_source = _usage(
                body, system_prompt, user_prompt, text
            )
            LLM_LATENCY.observe(elapsed)
            LLM_CALLS.labels(outcome="success").inc()
            return CompletionResult(
                text,
                int(elapsed * 1000),
                input_tokens,
                output_tokens,
                total_tokens,
                token_source,
                attempt_count=attempt,
            )
        except Exception as exc:  # noqa: BLE001 - retry transient, degrade on exhaustion
            last_exc = exc
            if attempt >= max_retries or not _is_retryable(exc):
                break
            delay = base_delay * (2 ** (attempt - 1))
            if on_retry is not None:
                await on_retry(attempt, max_retries, _error_code(exc), delay)
            logger.warning(
                "LLM call attempt %d/%d failed (transient), retrying in %.1fs: %s",
                attempt,
                max_retries,
                delay,
                exc,
            )
            await asyncio.sleep(delay)

    elapsed = time.monotonic() - started
    LLM_LATENCY.observe(elapsed)
    LLM_CALLS.labels(outcome="unavailable").inc()
    logger.warning(
        "LLM call failed after %d attempt(s), reporting unavailable: %s", attempt, last_exc
    )
    error_code = _error_code(last_exc)
    error_detail = f"Provider request failed at {endpoint}: {type(last_exc).__name__}."
    if isinstance(last_exc, ProviderHTTPError):
        error_code = f"http_{last_exc.status_code}"
        error_detail = (
            f"Provider rejected the request at {endpoint} with HTTP {last_exc.status_code}."
        )
    elif isinstance(last_exc, (json.JSONDecodeError, UnicodeDecodeError, TypeError)):
        error_code = "invalid_response"
        error_detail = f"Provider returned a non-JSON response from {endpoint}."
    elif isinstance(last_exc, TimeoutError):
        error_detail = (
            f"Provider request to {endpoint} timed out after {request_timeout:g}s "
            f"on {attempt} attempt(s)."
        )
    elif isinstance(last_exc, ProviderExecutionError):
        error_code = last_exc.code
        error_detail = f"Provider endpoint {endpoint} failed the outbound request policy."
    return CompletionResult(
        None,
        int(elapsed * 1000),
        None,
        None,
        None,
        "unavailable",
        error_code,
        error_detail,
        attempt,
    )


def _error_code(exc: Exception | None) -> str:
    if isinstance(exc, ProviderHTTPError):
        return f"http_{exc.status_code}"
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, TypeError)):
        return "invalid_response"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ProviderExecutionError):
        return exc.code
    return "provider_error"


def _openai_payload(
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    max_completion_tokens: int | None = None,
    organization_ref: str | None = None,
    project_ref: str | None = None,
    json_mode: bool = False,
    response_schema: ResponseSchema | None = None,
) -> tuple[dict, dict]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    if max_completion_tokens is not None:
        payload["max_completion_tokens"] = max_completion_tokens
    if response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.name,
                "strict": True,
                "schema": response_schema.schema,
            },
        }
    elif json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    if organization_ref:
        headers["OpenAI-Organization"] = organization_ref
    if project_ref:
        headers["OpenAI-Project"] = project_ref
    return payload, headers


def _extract_text(
    body: dict[str, Any],
    *,
    response_schema: ResponseSchema | None = None,
) -> str:
    choices = body.get("choices") or []
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "")
