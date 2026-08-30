"""Workspace readiness must prove the initial masked-data model route."""

from __future__ import annotations

import json
from types import SimpleNamespace

from lode.api.routes.control_plane import _model_role_coverage, _probe_model
from lode.engine.llm import CompletionResult


def row(*, allowed_data_classes: list[str], binding_revision: int = 1):
    binding = SimpleNamespace(
        id=1,
        state="active",
        revision=binding_revision,
        allowed_roles=["planner", "verifier"],
        allowed_data_classes=allowed_data_classes,
    )
    deployment = SimpleNamespace(state="active", availability_state="healthy")
    provider = SimpleNamespace(state="active", verification_status="healthy")
    return binding, deployment, provider


def test_model_role_coverage_requires_masked_data_class() -> None:
    roles, baseline_roles = _model_role_coverage(
        (row(allowed_data_classes=["source_code"]),),
        {1: 1},
    )

    assert roles == {"planner", "verifier"}
    assert baseline_roles == set()


def test_model_role_coverage_accepts_current_binding_revision() -> None:
    roles, baseline_roles = _model_role_coverage(
        (row(allowed_data_classes=["masked", "source_code"]),),
        {1: 1},
    )

    assert baseline_roles == roles == {"planner", "verifier"}


def test_model_role_coverage_rejects_stale_binding_revision() -> None:
    roles, baseline_roles = _model_role_coverage(
        (row(allowed_data_classes=["masked"], binding_revision=2),),
        {1: 1},
    )

    assert roles == baseline_roles == set()


async def test_model_probe_exercises_and_validates_the_strict_protocol(monkeypatch) -> None:
    captured = {}

    async def complete(*_args, response_schema, **_kwargs):
        captured["schema"] = response_schema.schema
        return CompletionResult(
            text=json.dumps(
                {
                    "ok": True,
                    "detail": {
                        "protocol": "structured_output",
                        "checks": ["required", "nullable", "nested"],
                    },
                    "nullable": None,
                }
            ),
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            token_source="provider",
            attempt_count=1,
        )

    monkeypatch.setattr("lode.api.routes.control_plane.complete_with_usage", complete)
    account = SimpleNamespace(
        protocol_id="openai.chat_completions.v1",
        base_url="https://model.example/v1",
        api_key_ciphertext="ciphertext",
    )

    assert await _probe_model(account, "gpt-test") == (True, None)
    assert captured["schema"]["required"] == ["ok", "detail", "nullable"]


async def test_model_probe_rejects_a_provider_that_ignores_the_schema(monkeypatch) -> None:
    async def complete(*_args, **_kwargs):
        return CompletionResult(
            text='{"ok":true}',
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            token_source="provider",
            attempt_count=1,
        )

    monkeypatch.setattr("lode.api.routes.control_plane.complete_with_usage", complete)
    account = SimpleNamespace(
        protocol_id="openai.chat_completions.v1",
        base_url="https://model.example/v1",
        api_key_ciphertext="ciphertext",
    )

    assert await _probe_model(account, "gpt-test") == (False, "invalid_response")
