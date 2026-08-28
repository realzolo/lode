"""Strict validation at the current control-plane API boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.api.control_schemas import (
    ConnectorCreate,
    GitAccountCreate,
    ModelBindingInput,
    ProviderAccountModelSelection,
    ModelPolicyInput,
    PlatformSettingsUpdate,
    ProviderAccountPatch,
    RepositoryBind,
    WorkspaceArchitectureContextPut,
    WorkspaceCreate,
    WorkspacePatch,
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
        (
            ProviderAccountModelSelection,
            {
                "models": [
                    {"provider_model_id": "gpt-5.6-sol", "source": "manual"},
                    {"provider_model_id": "gpt-5.6-sol", "source": "discovered"},
                ]
            },
        ),
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


def test_git_account_and_repository_binding_forms_require_complete_authorization() -> None:
    with pytest.raises(ValidationError):
        GitAccountCreate.model_validate(
            {
                "adapter_id": "github",
                "name": "GitHub",
            }
        )
    binding = RepositoryBind.model_validate(
        {
            "account_connection_id": 1,
            "repository_id": 2,
            "role": "runtime_source",
        }
    )
    assert binding.repository_id == 2


def test_provider_patch_rejects_removed_organization_and_project_fields() -> None:
    with pytest.raises(ValidationError):
        ProviderAccountPatch.model_validate({"organization_ref": "org"})


def test_provider_protocol_matrix_is_closed() -> None:
    from lode.api.control_schemas import ProviderAccountConnectionInput

    with pytest.raises(ValidationError):
        ProviderAccountConnectionInput.model_validate(
            {
                "provider_kind": "openai",
                "protocol_id": "anthropic.messages.v1",
                "base_url": "https://api.openai.com/v1",
                "api_key": "secret",
            }
        )


def test_entity_ids_are_positive_javascript_safe_integers() -> None:
    for value in (0, 2**52):
        with pytest.raises(ValidationError):
            RepositoryBind.model_validate(
                {
                    "account_connection_id": value,
                    "repository_id": 1,
                    "role": "runtime_source",
                }
            )


def test_ai_output_language_is_a_closed_set() -> None:
    assert PlatformSettingsUpdate(ai_output_language="zh", expected_revision=1).ai_output_language == "zh"
    with pytest.raises(ValidationError):
        PlatformSettingsUpdate(ai_output_language="ja", expected_revision=1)


def test_workspace_topic_patch_is_trimmed_and_nonblank() -> None:
    assert WorkspacePatch(ingestion_topic=" incident.checkout.v2 ").ingestion_topic == "incident.checkout.v2"
    with pytest.raises(ValidationError):
        WorkspacePatch(ingestion_topic="  ")


def test_workspace_description_and_architecture_context_are_bounded_structured_inputs() -> None:
    assert WorkspaceCreate(
        name="Checkout",
        description="Owns checkout incident investigation.",
        ingestion_topic="incident.checkout.v1",
    ).description.startswith("Owns checkout")
    context = WorkspaceArchitectureContextPut.model_validate(
        {
            "entries": [
                {
                    "kind": "critical_flow",
                    "title": "Checkout",
                    "content": "Gateway calls the order worker before payment authorization.",
                }
            ]
        }
    )
    assert context.entries[0].kind == "critical_flow"
    with pytest.raises(ValidationError):
        WorkspaceArchitectureContextPut.model_validate(
            {"entries": [{"kind": "freeform", "title": "x", "content": "y"}]}
        )
