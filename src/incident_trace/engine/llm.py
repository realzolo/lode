"""LLM client for the analysis agent.

The platform talks to OpenAI- or Anthropic-compatible chat-completion
endpoints. The endpoint, model, and key come from the ``ai_model_configs``
table (global default or per-application override).

If no model is configured, or the request fails for any reason, this module
returns ``None`` so the runner falls back to a deterministic, offline
heuristic. That keeps the product fully runnable without external API keys.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("incident_trace.engine.llm")


@dataclass
class ModelConfig:
    provider: str
    base_url: str
    api_key_ref: str
    model: str


async def complete(
    system_prompt: str,
    user_prompt: str,
    config: ModelConfig | None,
) -> str | None:
    """Return the assistant message text, or ``None`` if unavailable.

    The call is executed in a worker thread because ``urllib`` is blocking.
    """
    if config is None or not config.api_key_ref:
        return None

    if config.provider == "anthropic":
        payload, headers = _anthropic_payload(config, system_prompt, user_prompt)
    else:  # openai-compatible (default)
        payload, headers = _openai_payload(config, system_prompt, user_prompt)

    headers["Content-Type"] = "application/json"
    data = json.dumps(payload).encode("utf-8")

    def _post() -> str:
        req = urllib.request.Request(config.base_url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (internal)
            body = json.loads(resp.read().decode("utf-8"))
        return _extract_text(config.provider, body)

    try:
        return await _run_blocking(_post)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully to heuristic
        logger.warning("LLM call failed, using heuristic fallback: %s", exc)
        return None


def _openai_payload(config: ModelConfig, system: str, user: str) -> tuple[dict, dict]:
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key_ref}",
    }
    return payload, headers


def _anthropic_payload(config: ModelConfig, system: str, user: str) -> tuple[dict, dict]:
    # Anthropic Messages API uses a top-level system field (not a message role).
    base = config.base_url
    if not base.endswith("/messages"):
        base = base.rstrip("/") + "/messages"
    payload = {
        "model": config.model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": 1024,
    }
    headers = {
        "x-api-key": config.api_key_ref,
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
