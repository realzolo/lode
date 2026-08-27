from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lode.domain.errors import DomainValidationError
from lode.domain.models import (
    BuildUnit,
    Component,
    ContextPolicyRevision,
    EvidenceAccessScope,
    EvidenceArtifact,
    IdentityResolution,
    ModelBindingRevisionRef,
    ProviderAccountModel,
    ModelPolicyRevision,
    ObservedRelation,
    ResourceObservation,
    Workspace,
    WorkspaceModelBinding,
)
from lode.domain.types import (
    ComponentKind,
    ExecutionClass,
    HealthState,
    IdentityStatus,
    ModelRole,
    NativeLanguage,
    RelationKind,
    ResolutionKind,
    ResolutionStatus,
)


def test_workspace_requires_an_explicit_trimmed_topic() -> None:
    workspace = Workspace(name="Payments", ingestion_topic="incident.payments.v1")
    assert workspace.ingestion_version == 0

    with pytest.raises(DomainValidationError, match="ingestion_topic"):
        Workspace(name="Payments", ingestion_topic=" ")


def test_model_binding_requires_nonempty_roles_and_execution_classes() -> None:
    with pytest.raises(DomainValidationError) as exc:
        WorkspaceModelBinding(
            workspace_id=1,
            provider_account_model_id=2,
            execution_classes=(),
            allowed_roles=(ModelRole.PLANNER,),
            allowed_data_classes=("internal",),
            priority=0,
            max_calls=10,
            max_cost_per_call=1.0,
            timeout_ms=30000,
            max_context_utilization=0.8,
        )
    assert exc.value.code == "empty_collection"


@pytest.mark.parametrize("utilization", [0, 1, -0.1, 1.1])
def test_model_binding_reserves_context_capacity(utilization: float) -> None:
    with pytest.raises(DomainValidationError) as exc:
        WorkspaceModelBinding(
            workspace_id=1,
            provider_account_model_id=2,
            execution_classes=(ExecutionClass.LATENCY_OPTIMIZED,),
            allowed_roles=(ModelRole.PLANNER,),
            allowed_data_classes=("internal",),
            priority=0,
            max_calls=10,
            max_cost_per_call=1.0,
            timeout_ms=30000,
            max_context_utilization=utilization,
        )
    assert exc.value.code == "invalid_context_utilization"


def test_provider_account_model_requires_immutable_catalog_identity() -> None:
    account_model = ProviderAccountModel(
        provider_account_id=1,
        provider_model_id="gpt-5.6-sol",
        catalog_revision="openai-gpt5.6",
        catalog_profile_hash="a" * 64,
        discovery_state="discovered",
        availability_state=HealthState.HEALTHY,
    )

    with pytest.raises(FrozenInstanceError):
        account_model.catalog_revision = "next"  # type: ignore[misc]


def test_context_and_model_policy_require_versioned_nonempty_inputs() -> None:
    context = ContextPolicyRevision(
        pinned_evidence_kinds=("incident_input", "counter_evidence"),
        compression_levels=("deduplicate", "relevance", "summary"),
        minimum_output_tokens=4096,
        provider_safety_margin_tokens=1024,
    )
    policy = ModelPolicyRevision(
        workspace_id=1,
        eligible_bindings=(
            ModelBindingRevisionRef(binding_id=3, revision=1),
            ModelBindingRevisionRef(binding_id=4, revision=2),
        ),
        role_policies={"planner": {"execution_classes": ["latency_optimized"]}},
        context_policy_revision_id=1,
    )

    assert context.revision == 1
    assert policy.role_policies["planner"]["execution_classes"] == ("latency_optimized",)


@pytest.mark.parametrize("path", ["/root", "../escape", "a/../escape", "a//b", r"a\b"])
def test_build_unit_rejects_noncanonical_or_escaping_paths(path: str) -> None:
    with pytest.raises(DomainValidationError) as exc:
        BuildUnit(
            workspace_id=1,
            repository_binding_id=1,
            stable_key="repo:unit",
            source_root=path,
            build_system="python",
            manifest_paths=("pyproject.toml",),
            entrypoints=("src/main.py",),
            identity_status=IdentityStatus.PROVISIONAL,
        )
    assert exc.value.code == "invalid_repository_path"


def test_verified_component_requires_independent_provenance_families() -> None:
    with pytest.raises(DomainValidationError) as exc:
        Component(
            workspace_id=1,
            stable_key="component:worker",
            display_name="Worker",
            kind=ComponentKind.WORKER,
            identity_status=IdentityStatus.VERIFIED,
            root_provenance_families=("repository", "repository"),
        )
    assert exc.value.code == "insufficient_provenance"

    component = Component(
        workspace_id=1,
        stable_key="component:worker",
        display_name="Worker",
        kind=ComponentKind.WORKER,
        identity_status=IdentityStatus.VERIFIED,
        root_provenance_families=("repository", "deployment"),
    )
    assert component.identity_status is IdentityStatus.VERIFIED


def test_verified_identity_resolution_cannot_be_self_proved_by_one_root() -> None:
    with pytest.raises(DomainValidationError) as exc:
        IdentityResolution(
            workspace_id=1,
            stable_key="component:worker",
            resolution_kind=ResolutionKind.COMPONENT,
            status=ResolutionStatus.VERIFIED,
            resolved_payload={"kind": "worker"},
            observation_refs=(1, 2),
            annotation_refs=(1,),
            root_provenance_refs=("repo:1", "repo:1"),
            validator_version="identity-v1",
            resolution_hash="a" * 64,
        )
    assert exc.value.code == "insufficient_provenance"


def test_resource_observation_requires_timezone_and_sha256() -> None:
    with pytest.raises(DomainValidationError) as exc:
        ResourceObservation(
            workspace_id=1,
            source_kind="repository",
            source_ref="repo:1",
            observation_kind="manifest",
            structured_payload={},
            content_hash="short",
            root_provenance_id="repo:1@abc",
            source_family="repository",
            trust_class="declared_config",
            observed_at=datetime.now(UTC),
        )
    assert exc.value.code == "invalid_content_hash"


def test_evidence_scope_has_closed_native_languages_and_frozen_budget() -> None:
    scope = EvidenceAccessScope(
        connector_id=1,
        allowed_languages=(NativeLanguage.LOGQL,),
        scope_config={"root_selector": {"tenant": "payments"}},
        schema_catalog_revision=2,
        read_policy_revision=3,
        execution_budget_policy={"max_rows": 1000},
        normalization_policy_revision=4,
    )
    assert scope.allowed_languages == (NativeLanguage.LOGQL,)
    assert scope.scope_config["root_selector"]["tenant"] == "payments"


def test_causal_relations_require_explicit_evidence() -> None:
    with pytest.raises(DomainValidationError) as exc:
        ObservedRelation(
            investigation_id=1,
            source_entity_id=10,
            target_entity_id=11,
            kind=RelationKind.CALLED,
        )
    assert exc.value.code == "missing_relation_evidence"

    participation = ObservedRelation(
        investigation_id=1,
        source_entity_id=10,
        target_entity_id=11,
        kind=RelationKind.PARTICIPATED_IN,
    )
    assert participation.evidence_refs == ()


def test_evidence_artifact_is_immutable_and_timestamped() -> None:
    artifact = EvidenceArtifact(
        investigation_id=1,
        artifact_kind="normalized_input",
        content_hash="b" * 64,
        provenance={"source": "kafka"},
        evidence_class="runtime_observation",
        archived_at=datetime.now(UTC),
    )
    assert artifact.provenance["source"] == "kafka"


def test_domain_package_has_no_framework_or_provider_imports() -> None:
    domain_root = Path(__file__).resolve().parents[2] / "src" / "lode" / "domain"
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "pydantic",
        "aiokafka",
        "httpx",
        "asyncpg",
        "asyncmy",
    }
    imported: set[str] = set()
    for path in domain_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])

    assert imported.isdisjoint(forbidden_roots)
