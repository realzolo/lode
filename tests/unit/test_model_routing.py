from __future__ import annotations

import pytest

from lode.application.model_routing import (
    ModelCapabilityUnavailable,
    ModelSelectionPolicyEngine,
)
from lode.domain.model_execution import (
    ContextEvidence,
    ModelCandidate,
    ModelTask,
    highest_model_data_class,
    model_evidence_is_pinned,
)
from lode.domain.types import ExecutionClass, ModelRole
from lode.infrastructure.model_runtime import TokenizerRegistry


def candidate(
    snapshot_id: int,
    execution_class: ExecutionClass,
    *,
    role: ModelRole = ModelRole.PLANNER,
    cost: float = 0.1,
    priority: int = 0,
    provider_id: int | None = None,
    max_calls: int = 10,
    used_calls: int = 0,
    allowed_data_classes: tuple[str, ...] = ("masked",),
) -> ModelCandidate:
    return ModelCandidate(
        binding_snapshot_id=snapshot_id,
        workspace_model_binding_id=snapshot_id,
        provider_account_model_id=snapshot_id,
        provider_account_id=provider_id or snapshot_id,
        provider_account_revision=1,
        provider_account_model_revision=1,
        provider_model_id="gpt-5.6-sol",
        execution_classes=(execution_class,),
        allowed_roles=(role,),
        allowed_data_classes=allowed_data_classes,
        tokenizer_id="exact-json-bytes",
        context_window_tokens=20_000,
        max_output_tokens=2_000,
        provider_safety_margin_tokens=200,
        max_cost_per_call=2.0,
        max_context_utilization=0.8,
        priority=priority,
        health_status="healthy",
        predicted_cost=cost,
        max_calls=max_calls,
        used_calls=used_calls,
    )


def task(**overrides) -> ModelTask:
    values = {
        "role": ModelRole.PLANNER,
        "required_context_tokens": 1_000,
        "reserved_output_tokens": 1_000,
        "provider_safety_margin_tokens": 200,
        "data_class": "masked",
    }
    values.update(overrides)
    return ModelTask(**values)


def test_simple_task_uses_lowest_cost_latency_candidate() -> None:
    route = ModelSelectionPolicyEngine().select(
        task(requested_execution_class=ExecutionClass.REASONING_OPTIMIZED),
        (
            candidate(1, ExecutionClass.LATENCY_OPTIMIZED, cost=0.2),
            candidate(2, ExecutionClass.LATENCY_OPTIMIZED, cost=0.1),
            candidate(3, ExecutionClass.REASONING_OPTIMIZED, cost=0.01),
        ),
        remaining_calls=5,
        remaining_cost=10,
    )

    assert route.candidate.binding_snapshot_id == 2
    assert route.execution_class is ExecutionClass.LATENCY_OPTIMIZED


def test_server_complexity_requires_reasoning_and_does_not_fall_back() -> None:
    with pytest.raises(ModelCapabilityUnavailable) as exc:
        ModelSelectionPolicyEngine().select(
            task(component_count=2),
            (candidate(1, ExecutionClass.LATENCY_OPTIMIZED),),
            remaining_calls=5,
            remaining_cost=10,
        )

    assert exc.value.exclusions[0].code == "execution_class_unavailable"


def test_context_capacity_and_cost_are_server_enforced() -> None:
    constrained = candidate(1, ExecutionClass.LATENCY_OPTIMIZED, cost=3.0)
    with pytest.raises(ModelCapabilityUnavailable) as exc:
        ModelSelectionPolicyEngine().select(
            task(required_context_tokens=19_000),
            (constrained,),
            remaining_calls=1,
            remaining_cost=1,
        )

    assert exc.value.exclusions[0].code == "model_cost_budget_exceeded"


def test_verifier_can_require_a_different_provider() -> None:
    route = ModelSelectionPolicyEngine().select(
        task(
            role=ModelRole.VERIFIER,
            conclusion_risk="high",
            prior_synthesizer_account_model_id=10,
            prior_synthesizer_provider_id=7,
        ),
        (
            candidate(
                10,
                ExecutionClass.REASONING_OPTIMIZED,
                role=ModelRole.VERIFIER,
                provider_id=7,
            ),
            candidate(
                11,
                ExecutionClass.REASONING_OPTIMIZED,
                role=ModelRole.VERIFIER,
                provider_id=8,
            ),
        ),
        remaining_calls=2,
        remaining_cost=10,
        verifier_separate_provider=True,
    )

    assert route.candidate.provider_account_id == 8
    assert route.exclusions[0].code == "verifier_provider_not_independent"


def test_binding_call_budget_exhaustion_excludes_the_candidate() -> None:
    exhausted = candidate(
        1,
        ExecutionClass.LATENCY_OPTIMIZED,
        max_calls=2,
        used_calls=2,
    )

    with pytest.raises(ModelCapabilityUnavailable) as exc:
        ModelSelectionPolicyEngine().select(
            task(), (exhausted,), remaining_calls=10, remaining_cost=10
        )

    assert exc.value.exclusions[0].code == "model_binding_call_budget_exhausted"


def test_data_class_exclusion_records_requested_and_allowed_classes() -> None:
    with pytest.raises(ModelCapabilityUnavailable) as exc:
        ModelSelectionPolicyEngine().select(
            task(),
            (
                candidate(
                    1,
                    ExecutionClass.LATENCY_OPTIMIZED,
                    allowed_data_classes=("source_code",),
                ),
            ),
            remaining_calls=2,
            remaining_cost=10,
        )

    exclusion = exc.value.exclusions[0]
    assert exclusion.code == "data_class_not_allowed"
    assert dict(exclusion.detail) == {
        "required_execution_class": "latency_optimized",
        "requested_data_class": "masked",
        "allowed_data_classes": ("source_code",),
    }


def test_highest_model_data_class_uses_the_closed_runtime_order() -> None:
    source = ContextEvidence(
        artifact_id=1,
        artifact_kind="source_file",
        content={},
        token_count=0,
        relevance=1,
        data_class="source_code",
    )
    masked = ContextEvidence(
        artifact_id=2,
        artifact_kind="normalized_log_result",
        content={},
        token_count=0,
        relevance=1,
        data_class="masked",
    )
    restricted = ContextEvidence(
        artifact_id=3,
        artifact_kind="restricted_result",
        content={},
        token_count=0,
        relevance=1,
        data_class="restricted",
    )

    assert highest_model_data_class(()) == "masked"
    assert highest_model_data_class((source, masked)) == "source_code"
    assert highest_model_data_class((source, restricted, masked)) == "restricted"


def test_incident_input_is_always_pinned_for_model_context() -> None:
    assert model_evidence_is_pinned("incident_input", set())
    assert model_evidence_is_pinned("source_file", {"source_file"})
    assert not model_evidence_is_pinned("source_file", set())


def test_tokenizer_registry_fails_closed_for_unregistered_deployments() -> None:
    registry = TokenizerRegistry()

    assert registry.supports("exact-json-bytes")
    assert not registry.supports("unregistered-provider-tokenizer")
    with pytest.raises(RuntimeError, match="tokenizer is not registered"):
        registry.require("unregistered-provider-tokenizer")
