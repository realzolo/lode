"""Strict validation at the current control-plane API boundary."""

from __future__ import annotations

import ssl

import pytest
from pydantic import TypeAdapter, ValidationError

from lode.api.control_schemas import (
    ConnectorCreate,
    GitAccountCreate,
    ModelBindingInput,
    ProviderAccountModelSelection,
    ModelPolicyInput,
    PlatformSettingsUpdate,
    ProviderAccountPatch,
    RepositoryBind,
    RepositoryBindingPatch,
    WorkspaceArchitectureContextPut,
    WorkspaceCreate,
    WorkspacePatch,
)


def system_ca_pem() -> str:
    certificate = ssl.create_default_context().get_ca_certs(binary_form=True)[0]
    return ssl.DER_cert_to_PEM_cert(certificate)


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
        TypeAdapter(ConnectorCreate).validate_python(
            {
                "name": "logs",
                "kind": "loki",
                "config": {"base_url": "https://logs.example.com"},
                "scope_config": {"root_matchers": {"cluster": "production"}},
            }
        )


def test_connector_creation_uses_strict_kind_specific_contracts() -> None:
    adapter = TypeAdapter(ConnectorCreate)
    postgresql = adapter.validate_python(
        {
            "name": "warehouse",
            "kind": "postgresql",
            "host": "replica.example.test",
            "database": "analytics",
            "database_username": "lode_reader",
            "database_password": "secret",
            "tls_mode": "verify_full",
            "ca_certificate_pem": system_ca_pem(),
            "allowed_schemas": ["billing", "public"],
        }
    )
    assert postgresql.allowed_schemas == ("billing", "public")
    assert postgresql.ca_certificate_pem == system_ca_pem()

    pooler = adapter.validate_python(
        {
            "name": "supabase-pooler",
            "kind": "postgresql",
            "host": "aws-1-us-east-1.pooler.supabase.com",
            "database": "postgres",
            "database_username": "postgres.project-ref",
            "database_password": "secret",
            "tls_mode": "require",
            "allowed_schemas": ["public"],
        }
    )
    assert pooler.database_username == "postgres.project-ref"
    assert pooler.tls_mode == "require"

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "name": "warehouse",
                "kind": "postgresql",
                "host": "replica.example.test",
                "database": "analytics",
                "database_username": "lode_reader",
                "database_password": "secret",
                "tls_mode": "verify_full",
                "allowed_schemas": ["public"],
                "endpoint": "https://unexpected.example.test",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "name": "logs",
            "kind": "loki",
            "endpoint": "https://loki.example.test",
            "authentication": "none",
            "root_filter": {
                "kind": "group",
                "combinator": "all",
                "items": [
                    {
                        "kind": "condition",
                        "label": "app",
                        "operator": "any_of",
                        "values": ["payments", "checkout"],
                    }
                ],
            },
        },
        {
            "name": "elastic",
            "kind": "elasticsearch",
            "endpoint": "https://elastic.example.test",
            "authentication": "api_key",
            "credential": "secret",
            "allowed_indices": ["logs-production"],
        },
        {
            "name": "open-search",
            "kind": "opensearch",
            "endpoint": "https://search.example.test",
            "authentication": "bearer_token",
            "credential": "secret",
            "allowed_indices": ["events-2026"],
        },
        {
            "name": "postgres",
            "kind": "postgresql",
            "host": "replica.example.test",
            "database": "analytics",
            "database_username": "lode_reader",
            "database_password": "secret",
            "tls_mode": "verify_full",
            "allowed_schemas": ["public"],
        },
        {
            "name": "mysql",
            "kind": "mysql",
            "host": "replica.example.test",
            "database": "analytics",
            "database_username": "lode_reader",
            "database_password": "secret",
            "tls_mode": "require",
        },
        {
            "name": "events",
            "kind": "https",
            "endpoint": "https://events.example.test",
            "authentication": "basic",
            "credential_username": "lode_reader",
            "credential": "secret",
            "safe_read_path": "/v1/events",
        },
    ],
)
def test_connector_creation_accepts_each_strict_variant(payload) -> None:
    validated = TypeAdapter(ConnectorCreate).validate_python(payload)
    assert validated.kind == payload["kind"]


@pytest.mark.parametrize(
    "allowed_schemas",
    [[], ["public", "public"], ["pg_catalog"], ["pg_toast_temp_1"], ["pg_temp_1"], ["billing*"]],
)
def test_postgresql_creation_rejects_invalid_schema_scopes(allowed_schemas) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ConnectorCreate).validate_python(
            {
                "name": "warehouse",
                "kind": "postgresql",
                "host": "replica.example.test",
                "database": "analytics",
                "database_username": "lode_reader",
                "database_password": "secret",
                "tls_mode": "verify_full",
                "allowed_schemas": allowed_schemas,
            }
        )


@pytest.mark.parametrize(
    "kind,invalid_ca",
    [
        ("postgresql", "not a PEM certificate"),
        ("mysql", "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----"),
    ],
)
def test_database_creation_rejects_invalid_ca_configuration(
    kind: str, invalid_ca: str
) -> None:
    payload = {
        "name": "warehouse",
        "kind": kind,
        "host": "replica.example.test",
        "database": "analytics",
        "database_username": "lode_reader",
        "database_password": "secret",
        "tls_mode": "verify_full",
        "ca_certificate_pem": invalid_ca,
    }
    if kind == "postgresql":
        payload["allowed_schemas"] = ["public"]

    with pytest.raises(ValidationError):
        TypeAdapter(ConnectorCreate).validate_python(payload)


def test_database_creation_rejects_ca_when_server_identity_is_not_verified() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ConnectorCreate).validate_python(
            {
                "name": "warehouse",
                "kind": "postgresql",
                "host": "replica.example.test",
                "database": "analytics",
                "database_username": "lode_reader",
                "database_password": "secret",
                "tls_mode": "require",
                "ca_certificate_pem": system_ca_pem(),
                "allowed_schemas": ["public"],
            }
        )


@pytest.mark.parametrize(
    "index",
    ["logs-*", "logs,errors", "../logs", "logs..archive", "Logs", ".internal", "_all"],
)
def test_search_creation_requires_exact_non_reserved_indices(index: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ConnectorCreate).validate_python(
            {
                "name": "search",
                "kind": "elasticsearch",
                "endpoint": "https://search.example.test",
                "authentication": "api_key",
                "credential": "secret",
                "allowed_indices": [index],
            }
        )


def test_https_creation_accepts_complete_basic_authentication() -> None:
    payload = TypeAdapter(ConnectorCreate).validate_python(
        {
            "name": "events",
            "kind": "https",
            "endpoint": "https://events.example.test",
            "authentication": "basic",
            "credential_username": "lode_reader",
            "credential": "secret",
            "safe_read_path": "/v1/events",
        }
    )
    assert payload.authentication == "basic"


def test_non_basic_connector_authentication_rejects_a_username() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ConnectorCreate).validate_python(
            {
                "name": "events",
                "kind": "https",
                "endpoint": "https://events.example.test",
                "authentication": "bearer_token",
                "credential_username": "unused",
                "credential": "secret",
                "safe_read_path": "/v1/events",
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


def test_repository_branch_policy_requires_a_valid_fixed_branch() -> None:
    fixed = RepositoryBind.model_validate(
        {
            "account_connection_id": 1,
            "repository_id": 2,
            "role": "runtime_source",
            "branch_mode": "branch",
            "branch_name": "release/2026.08",
        }
    )
    assert fixed.branch_name == "release/2026.08"
    assert RepositoryBind.model_validate(
        {"account_connection_id": 1, "repository_id": 2, "role": "runtime_source"}
    ).branch_mode == "default"
    with pytest.raises(ValidationError):
        RepositoryBind.model_validate(
            {"account_connection_id": 1, "repository_id": 2, "role": "runtime_source", "branch_mode": "branch"}
        )
    with pytest.raises(ValidationError):
        RepositoryBind.model_validate(
            {"account_connection_id": 1, "repository_id": 2, "role": "runtime_source", "branch_name": "main"}
        )
    with pytest.raises(ValidationError):
        RepositoryBindingPatch.model_validate({"role": "runtime_source"})


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
