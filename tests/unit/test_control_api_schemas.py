"""Strict validation at the current control-plane API boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.api.control_schemas import (
    ConnectorCreate,
    GitProviderInstanceCreate,
    WorkspaceGitAccountGrantCreate,
    ModelBindingInput,
    ProviderAccountModelSelection,
    ModelPolicyInput,
    PlatformSettingsUpdate,
    ProviderAccountPatch,
    InvestigationPolicyPut,
)


def test_model_binding_rejects_duplicate_roles() -> None:
    values = {
        "provider_account_model_id": 1,
        "execution_classes": ["latency_optimized"],
        "allowed_roles": ["planner", "planner"],
        "max_calls": 2,
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
        (ProviderAccountModelSelection, {"model_ids": ["gpt-5.6-sol"], "manual_model_ids": ["missing"]}),
    ],
)
def test_patch_rejects_explicit_null_for_required_storage_fields(schema, values) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(values)


def test_connector_creation_rejects_raw_configuration_documents() -> None:
    with pytest.raises(ValidationError):
        ConnectorCreate.model_validate(
            {
                "name": "logs",
                "kind": "loki",
                "config": {"base_url": "https://logs.example.com"},
                "scope_config": {"root_matchers": {"cluster": "production"}},
            }
        )


def test_git_provider_and_workplace_access_forms_require_complete_authorization() -> None:
    with pytest.raises(ValidationError):
        GitProviderInstanceCreate.model_validate(
            {
                "kind": "github",
                "name": "GitHub",
                "github_app_id": "123",
            }
        )
    with pytest.raises(ValidationError):
        WorkspaceGitAccountGrantCreate.model_validate(
            {"account_connection_id": 1, "repository_scope": "selected"}
        )


def test_provider_patch_allows_clearing_optional_scope_references() -> None:
    patch = ProviderAccountPatch.model_validate(
        {"organization_ref": None, "project_ref": None}
    )

    assert patch.model_fields_set == {"organization_ref", "project_ref"}


def test_investigation_profile_and_ai_output_language_are_closed_sets() -> None:
    assert InvestigationPolicyPut(profile="balanced").profile == "balanced"
    assert PlatformSettingsUpdate(ai_output_language="zh", expected_revision=1).ai_output_language == "zh"
    with pytest.raises(ValidationError):
        InvestigationPolicyPut(profile="custom")
    with pytest.raises(ValidationError):
        PlatformSettingsUpdate(ai_output_language="ja", expected_revision=1)
