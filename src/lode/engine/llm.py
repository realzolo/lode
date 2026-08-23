"""LLM client for the analysis agent.

The platform talks to OpenAI- or Anthropic-compatible chat-completion
endpoints. The endpoint, model, and key come from the ``ai_model_configs``
table (global default or per-application override).

If no model is configured, or the request fails for any reason, this module
returns ``None`` so the runner falls back to a deterministic, offline
heuristic. That keeps the product fully runnable without external API keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from lode.config import settings
from lode.crypto import decrypt_secret
from lode.metrics import LLM_CALLS, LLM_LATENCY

logger = logging.getLogger("lode.engine.llm")


@dataclass
class ModelConfig:
    provider: str
    base_url: str
    api_key_ref: str
    model: str


def resolve_api_key(api_key_ref: str) -> str:
    """Resolve an ``api_key_ref`` to the actual secret value.

    Two forms are supported:

    * ``env://NAME`` — read the key from the environment variable ``NAME``.
      This is the recommended form so real credentials never touch the
      database and are injected per-deployment.
    * an encrypted literal — a Fernet token produced by ``encrypt_secret``,
      decrypted back to the plaintext key. Literal keys are stored encrypted at
      rest so the plaintext never lands in the database row.

    Any existing plaintext literal rows are re-encrypted once at startup (see
    ``lode.api.main``), so this path always receives an encrypted value and has
    no plaintext fallback. Returns an empty string when an ``env://`` reference
    points at an unset variable, so the caller gracefully falls back to the
    heuristic engine.
    """
    if api_key_ref.startswith("env://"):
        name = api_key_ref[len("env://") :]
        value = os.environ.get(name)
        if not value:
            logger.warning("api_key_ref references unset environment variable %s", name)
            return ""
        return value
    return decrypt_secret(api_key_ref) or ""


def _is_retryable(exc: Exception) -> bool:
    """Classify a failure as transient (worth retrying) or fatal.

    * Network errors (DNS, refused, reset) and timeouts are transient.
    * HTTP 5xx from the provider (overload, gateway) are transient.
    * HTTP 4xx are client errors (bad key, bad request) — retrying cannot fix
      them, so we fail closed to the heuristic immediately.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return 500 <= getattr(exc, "code", 0) <= 599
    return True


async def complete(
    system_prompt: str,
    user_prompt: str,
    config: ModelConfig | None,
) -> str | None:
    """Return the assistant message text, or ``None`` if unavailable.

    The call is executed in a worker thread because ``urllib`` is blocking.
    Transient failures (network blips, provider 5xx) are retried with bounded
    exponential backoff; after exhausting retries the engine degrades to the
    deterministic offline heuristic so the product never hard-fails on an LLM
    outage.
    """
    if config is None or not config.api_key_ref:
        return None

    # Resolve the reference form (env://NAME) to the real secret before any
    # network call. If it resolves empty, degrade to the offline heuristic.
    api_key = resolve_api_key(config.api_key_ref)
    if not api_key:
        return None

    if config.provider == "anthropic":
        payload, headers = _anthropic_payload(api_key, config.base_url, config.model, system_prompt, user_prompt)
    else:  # openai-compatible (default)
        payload, headers = _openai_payload(api_key, config.base_url, config.model, system_prompt, user_prompt)

    headers["Content-Type"] = "application/json"
    data = json.dumps(payload).encode("utf-8")

    def _post() -> str:
        req = urllib.request.Request(config.base_url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return _extract_text(config.provider, body)

    max_retries = settings.llm_max_retries
    base_delay = settings.llm_retry_base_delay
    last_exc: Exception | None = None
    # Time only the network round-trip(s); retries add their own sleep that is
    # not part of "provider latency". A successful call reports one observation.
    started = time.monotonic()
    for attempt in range(1, max_retries + 1):
        try:
            text = await _run_blocking(_post)
            LLM_LATENCY.observe(time.monotonic() - started)
            LLM_CALLS.labels(outcome="success").inc()
            return text
        except Exception as exc:  # noqa: BLE001 - retry transient, degrade on exhaustion
            last_exc = exc
            if attempt >= max_retries or not _is_retryable(exc):
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "LLM call attempt %d/%d failed (transient), retrying in %.1fs: %s",
                attempt, max_retries, delay, exc,
            )
            await asyncio.sleep(delay)

    LLM_LATENCY.observe(time.monotonic() - started)
    LLM_CALLS.labels(outcome="fallback").inc()
    logger.warning("LLM call failed after %d attempt(s), using heuristic fallback: %s",
                   max_retries, last_exc)
    return None

def _openai_payload(api_key: str, base_url: str, model: str, system: str, user: str) -> tuple[dict, dict]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    return payload, headers


def _anthropic_payload(api_key: str, base_url: str, model: str, system: str, user: str) -> tuple[dict, dict]:
    # Anthropic Messages API uses a top-level system field (not a message role).
    base = base_url
    if not base.endswith("/messages"):
        base = base.rstrip("/") + "/messages"
    payload = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": 1024,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    return payload, headers


def _extract_text(provider: str, body: dict[str, Any]) -> str:
    if provider == "anthropic":
        parts = body.get("content") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    choices = body.get("choices") or []
    if not choices:
        return ""
    return choices[0].get("message", {}).get("content", "")


async def _run_blocking(fn) -> str:
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn)
