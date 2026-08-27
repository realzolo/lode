"""Strict validation at the current control-plane API boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.api.control_schemas import (
    ConnectorPatch,
    ModelBindingInput,
    ModelDeploymentPatch,
    ModelPolicyInput,
    PlatformSettingsUpdate,
    ProviderAccountPatch,
    InvestigationPolicyPut,
)


def test_model_binding_rejects_duplicate_roles_and_invalid_token_split() -> None:
    values = {
        "model_deployment_id": 1,
        "execution_classes": ["latency_optimized"],
        "allowed_roles": ["planner", "planner"],
        "max_calls": 2,
        "max_input_tokens": 1_000,
        "max_output_tokens": 1_000,
        "max_cost_per_call": 1,
        "timeout_ms": 1_000,
        "allowed_data_classes": ["masked_operational"],
        "max_context_utilization": 0.8,
    }
    with pytest.raises(ValidationError):
        ModelBindingInput.model_validate(values)


def test_model_policy_rejects_duplicate_immutable_binding_refs() -> None:
    with pytest.raises(ValidationError):
        ModelPolicyInput(
            eligible_binding_ids=[1, 1],
            role_policies={},
            pinned_evidence_kinds=["incident_input"],
            compression_levels=["extractive"],
            minimum_output_tokens=128,
            provider_safety_margin_tokens=64,
        )


@pytest.mark.parametrize(
    ("schema", "values"),
    [
        (ProviderAccountPatch, {"name": None}),
        (ModelDeploymentPatch, {"capabilities": None}),
        (ConnectorPatch, {"secrets": None}),
    ],
)
def test_patch_rejects_explicit_null_for_required_storage_fields(schema, values) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(values)


def test_provider_patch_allows_clearing_optional_scope_references() -> None:
    patch = ProviderAccountPatch.model_validate(
        {"organization_ref": None, "project_ref": None, "tenant_ref": None}
    )

    assert patch.model_fields_set == {"organization_ref", "project_ref", "tenant_ref"}


def test_investigation_profile_and_ai_output_language_are_closed_sets() -> None:
    assert InvestigationPolicyPut(profile="balanced").profile == "balanced"
    assert PlatformSettingsUpdate(ai_output_language="zh", expected_revision=1).ai_output_language == "zh"
    with pytest.raises(ValidationError):
        InvestigationPolicyPut(profile="custom")
    with pytest.raises(ValidationError):
        PlatformSettingsUpdate(ai_output_language="ja", expected_revision=1)
