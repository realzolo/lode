"""Protocol-level AI model availability checks shared by control-plane routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from lode.config import settings
from lode.db.models.ai_model import AiModelConfig
from lode.engine.llm import ModelConfig, complete_with_usage, model_endpoint


@dataclass(frozen=True)
class ModelHealth:
    available: bool
    endpoint: str
    latency_ms: int
    error_code: str | None
    error_detail: str | None


async def probe_model(model: AiModelConfig) -> ModelHealth:
    config = ModelConfig(
        provider=model.provider,
        base_url=model.base_url,
        api_key_ciphertext=model.api_key_ciphertext,
        model=model.model,
    )
    result = await complete_with_usage(
        "You are an API availability probe.",
        "Reply with exactly OK.",
        config,
        timeout_seconds=settings.llm_probe_timeout_seconds,
    )
    return ModelHealth(
        available=bool(result.text and result.text.strip()),
        endpoint=model_endpoint(model.provider, model.base_url),
        latency_ms=result.latency_ms,
        error_code=result.error_code,
        error_detail=result.error_detail,
    )


def record_model_health(model: AiModelConfig, health: ModelHealth) -> None:
    model.last_test_status = "available" if health.available else "unavailable"
    model.last_tested_at = datetime.now(UTC)
    model.last_test_latency_ms = health.latency_ms
    model.last_test_error_code = health.error_code
    model.last_test_error_detail = health.error_detail
