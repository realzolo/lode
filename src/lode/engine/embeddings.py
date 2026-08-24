"""Embeddings client for semantic shared experience.

Produces dense vectors for incident signatures and experience content via an
OpenAI-compatible ``/embeddings`` endpoint. Like the chat client in
``lode.engine.llm``, the key reference is resolved through ``resolve_api_key``
(``env://NAME`` or a literal) and any failure degrades to ``None`` so the
runner can fall back to the exact trigger_signature match instead of crashing.

The returned vectors are dimension-agnostic here; the column/index dimension is
fixed in ``lode.db.vector`` (EMBEDDING_DIM). Callers are responsible for
configuring a model whose output dimension matches that constant.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.request
from dataclasses import dataclass
from typing import Any

from lode.db.vector import EMBEDDING_DIM
from lode.engine.llm import resolve_api_key

logger = logging.getLogger("lode.engine.embeddings")


@dataclass
class EmbeddingConfig:
    base_url: str
    api_key_ref: str
    model: str
    dimensions: int = EMBEDDING_DIM


def _run_blocking(fn) -> Any:
    import asyncio

    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, fn)


async def embed(text: str, config: EmbeddingConfig | None) -> list[float] | None:
    """Return the embedding vector for ``text``, or ``None`` if unavailable.

    Unavailable means either no config was supplied, the key reference resolves
    empty, or the network call fails for any reason. The caller should then
    gracefully fall back to exact-match experience retrieval.
    """
    if config is None or not config.api_key_ref:
        return None

    api_key = resolve_api_key(config.api_key_ref)
    if not api_key:
        return None

    url = config.base_url
    if not url.rstrip("/").endswith("/embeddings"):
        url = url.rstrip("/") + "/embeddings"

    payload: dict[str, Any] = {"model": config.model, "input": text}
    # text-embedding-3-* supports a dimensionality cap; older models ignore it.
    if config.dimensions:
        payload["dimensions"] = config.dimensions

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8")

    def _post() -> list[float]:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (internal)
            body = json.loads(resp.read().decode("utf-8"))
        items = body.get("data") or []
        if not items:
            return []
        return [float(x) for x in items[0].get("embedding", [])]

    try:
        return await _run_blocking(_post)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully to exact match
        logger.warning("embedding call failed, using exact match: %s", exc)
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 for degenerate vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def cosine_distance(a: list[float], b: list[float]) -> float:
    """Cosine distance in [0, 2]; 0 means identical direction."""
    return 1.0 - cosine_similarity(a, b)


def build_query_text(alert) -> str:
    """Build a semantic query string from an alert's salient fields.

    This is what gets embedded for both retrieval (find similar past incidents)
    and storage (the embedding stored on a new experience row describes *what kind*
    of incident this is, independent of the stored conclusion text).
    """
    if alert is None:
        return ""

    title = getattr(alert, "title", "") or ""
    level = getattr(alert, "level", "") or ""
    error = getattr(alert, "error_message", "") or ""
    fields = getattr(alert, "fields", {}) or {}

    lines = [
        f"Incident: {title}".strip(),
        f"Level: {level}",
        f"Error: {error}",
    ]
    if isinstance(fields, dict) and fields:
        # Keep the query compact: a handful of the most informative field pairs.
        ctx = "; ".join(f"{k}={v}" for k, v in list(fields.items())[:8])
        lines.append(f"Context: {ctx}")
    return "\n".join(line for line in lines if line).strip()
