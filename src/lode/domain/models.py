"""Pure, immutable domain records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

from lode.domain.errors import DomainValidationError
from lode.domain.evidence_budget import ExecutionBudgetPolicy
from lode.domain.types import (
    EVIDENCE_REQUIRED_RELATIONS,
    ComponentKind,
    ExecutionClass,
    HealthState,
    IdentityStatus,
    IngestionState,
    LifecycleState,
    ModelRole,
    NativeLanguage,
    RelationKind,
    RepositoryRole,
    ResolutionKind,
    ResolutionStatus,
    SourceBindingRole,
)

_STABLE_KEY = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required(value: str, field_name: str) -> str:
    if not value or value != value.strip():
        raise DomainValidationError("invalid_text", f"{field_name} must be non-blank and trimmed")
    return value


def _stable_key(value: str, field_name: str = "stable_key") -> str:
    if not _STABLE_KEY.fullmatch(value):
        raise DomainValidationError("invalid_stable_key", f"{field_name} is not canonical")
    return value


def _unique_nonempty(values: tuple[Any, ...], field_name: str) -> None:
    if not values:
        raise DomainValidationError("empty_collection", f"{field_name} must not be empty")
    if len(values) != len(set(values)):
        raise DomainValidationError("duplicate_value", f"{field_name} must contain unique values")


def _repository_path(value: str, field_name: str) -> str:
    if not value or "\\" in value or value.startswith("/"):
        raise DomainValidationError("invalid_repository_path", f"{field_name} must be relative")
    path = PurePosixPath(value)
    if any(part in {"", ".."} for part in path.parts):
        raise DomainValidationError("invalid_repository_path", f"{field_name} escapes repository")
    normalized = path.as_posix()
    if normalized != value or normalized == "":
        raise DomainValidationError("invalid_repository_path", f"{field_name} must be canonical")
    return normalized


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class Workspace:
    name: str
    ingestion_topic: str
    ingestion_state: IngestionState = IngestionState.DRAFT
    ingestion_version: int = 0
    ingestion_start_position: str | None = None

    def __post_init__(self) -> None:
        _required(self.name, "name")
        _required(self.ingestion_topic, "ingestion_topic")
        if self.ingestion_version < 0:
            raise DomainValidationError(
                "invalid_revision", "ingestion_version must be non-negative"
            )
        if self.ingestion_start_position not in {None, "earliest", "latest"}:
            raise DomainValidationError(
                "invalid_start_position", "invalid ingestion start position"
            )


@dataclass(frozen=True, slots=True)
class ProviderAccount:
    name: str
    provider_kind: Literal["openai", "anthropic"]
    protocol_id: str
    base_url: str
    state: LifecycleState = LifecycleState.ACTIVE
    verification_status: HealthState = HealthState.UNTESTED
    revision: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            "name",
            "provider_kind",
            "protocol_id",
            "base_url",
        ):
            _required(getattr(self, field_name), field_name)
        if self.protocol_id not in {
            "openai.responses.v1",
            "openai.chat_completions.v1",
            "anthropic.messages.v1",
        }:
            raise DomainValidationError("unsupported_protocol", "model protocol is not supported")
        if (
            self.provider_kind == "openai"
            and not self.protocol_id.startswith("openai.")
        ) or (
            self.provider_kind == "anthropic"
            and self.protocol_id != "anthropic.messages.v1"
        ):
            raise DomainValidationError(
                "provider_protocol_mismatch", "model protocol does not match provider"
            )
        if self.revision < 1:
            raise DomainValidationError("invalid_revision", "revision must be positive")


@dataclass(frozen=True, slots=True)
class ProviderAccountModel:
    provider_account_id: int
    provider_model_id: str
    catalog_revision: str
    catalog_profile_hash: str
    discovery_state: Literal["discovered", "manual", "missing"]
    availability_state: HealthState = HealthState.UNTESTED
    state: LifecycleState = LifecycleState.ACTIVE
    revision: int = 1

    def __post_init__(self) -> None:
        if self.provider_account_id < 1:
            raise DomainValidationError("invalid_reference", "provider account must be positive")
        _required(self.provider_model_id, "provider_model_id")
        _required(self.catalog_revision, "catalog_revision")
        _required(self.catalog_profile_hash, "catalog_profile_hash")
        if self.discovery_state not in {"discovered", "manual", "missing"}:
            raise DomainValidationError(
                "invalid_discovery_state", "account model discovery state is invalid"
            )
        if not _SHA256.fullmatch(self.catalog_profile_hash) or self.revision < 1:
            raise DomainValidationError(
                "invalid_model_catalog", "account model catalog data is invalid"
            )


@dataclass(frozen=True, slots=True)
class WorkspaceModelBinding:
    workspace_id: int
    provider_account_model_id: int
    execution_classes: tuple[ExecutionClass, ...]
    allowed_roles: tuple[ModelRole, ...]
    allowed_data_classes: tuple[str, ...]
    priority: int
    max_calls: int
    max_cost_per_call: float
    timeout_ms: int
    max_context_utilization: float
    state: LifecycleState = LifecycleState.ACTIVE
    revision: int = 1

    def __post_init__(self) -> None:
        if min(self.workspace_id, self.provider_account_model_id) < 1:
            raise DomainValidationError("invalid_reference", "binding references must be positive")
        _unique_nonempty(self.execution_classes, "execution_classes")
        _unique_nonempty(self.allowed_roles, "allowed_roles")
        _unique_nonempty(self.allowed_data_classes, "allowed_data_classes")
        if (
            self.priority < 0
            or min(
                self.max_calls,
                self.timeout_ms,
                self.revision,
            )
            < 1
        ):
            raise DomainValidationError("invalid_model_limit", "binding limits are invalid")
        if self.max_cost_per_call < 0:
            raise DomainValidationError("invalid_model_limit", "max cost must be non-negative")
        if not 0 < self.max_context_utilization < 1:
            raise DomainValidationError(
                "invalid_context_utilization",
                "max context utilization must be between zero and one",
            )


@dataclass(frozen=True, slots=True)
class ContextPolicyRevision:
    pinned_evidence_kinds: tuple[str, ...]
    compression_levels: tuple[str, ...]
    minimum_output_tokens: int
    provider_safety_margin_tokens: int
    revision: int = 1

    def __post_init__(self) -> None:
        _unique_nonempty(self.pinned_evidence_kinds, "pinned_evidence_kinds")
        _unique_nonempty(self.compression_levels, "compression_levels")
        if min(self.minimum_output_tokens, self.provider_safety_margin_tokens, self.revision) < 1:
            raise DomainValidationError(
                "invalid_context_policy", "context policy limits must be positive"
            )


@dataclass(frozen=True, slots=True)
class ModelBindingRevisionRef:
    binding_id: int
    revision: int

    def __post_init__(self) -> None:
        if min(self.binding_id, self.revision) < 1:
            raise DomainValidationError(
                "invalid_reference", "model binding revision references must be positive"
            )


@dataclass(frozen=True, slots=True)
class ModelPolicyRevision:
    workspace_id: int
    eligible_bindings: tuple[ModelBindingRevisionRef, ...]
    role_policies: Mapping[str, Any]
    context_policy_revision_id: int
    revision: int = 1

    def __post_init__(self) -> None:
        if min(self.workspace_id, self.context_policy_revision_id, self.revision) < 1:
            raise DomainValidationError("invalid_reference", "policy references must be positive")
        _unique_nonempty(self.eligible_bindings, "eligible_bindings")
        binding_ids = tuple(value.binding_id for value in self.eligible_bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise DomainValidationError(
                "duplicate_value", "eligible model binding IDs must be unique"
            )
        object.__setattr__(self, "role_policies", _freeze(self.role_policies))


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    workspace_id: int
    repository_id: int
    role: RepositoryRole
    priority: int
    descriptor_revision: int
    description: str = ""
    state: LifecycleState = LifecycleState.ACTIVE

    def __post_init__(self) -> None:
        if min(self.workspace_id, self.repository_id, self.descriptor_revision) < 1:
            raise DomainValidationError(
                "invalid_reference", "repository references must be positive"
            )
        if self.priority < 0:
            raise DomainValidationError("invalid_priority", "priority must be non-negative")
        if self.description != self.description.strip():
            raise DomainValidationError("invalid_text", "description must be trimmed")


@dataclass(frozen=True, slots=True)
class BuildUnit:
    workspace_id: int
    repository_binding_id: int
    stable_key: str
    source_root: str
    build_system: str
    manifest_paths: tuple[str, ...]
    entrypoints: tuple[str, ...]
    identity_status: IdentityStatus
    revision: int = 1

    def __post_init__(self) -> None:
        if min(self.workspace_id, self.repository_binding_id, self.revision) < 1:
            raise DomainValidationError(
                "invalid_reference", "build unit references must be positive"
            )
        _stable_key(self.stable_key)
        _repository_path(self.source_root, "source_root")
        _required(self.build_system, "build_system")
        if len(self.manifest_paths) != len(set(self.manifest_paths)):
            raise DomainValidationError("duplicate_value", "manifest paths must be unique")
        for path in self.manifest_paths + self.entrypoints:
            _repository_path(path, "build_unit_path")


@dataclass(frozen=True, slots=True)
class Component:
    workspace_id: int
    stable_key: str
    display_name: str
    kind: ComponentKind
    identity_status: IdentityStatus
    root_provenance_families: tuple[str, ...]
    revision: int = 1

    def __post_init__(self) -> None:
        if min(self.workspace_id, self.revision) < 1:
            raise DomainValidationError(
                "invalid_reference", "component references must be positive"
            )
        _stable_key(self.stable_key)
        _required(self.display_name, "display_name")
        families = set(self.root_provenance_families)
        if self.identity_status is IdentityStatus.VERIFIED and len(families) < 2:
            raise DomainValidationError(
                "insufficient_provenance", "verified component requires two provenance families"
            )


@dataclass(frozen=True, slots=True)
class ComponentSourceBinding:
    component_id: int
    build_unit_id: int
    role: SourceBindingRole
    path_prefix: str

    def __post_init__(self) -> None:
        if min(self.component_id, self.build_unit_id) < 1:
            raise DomainValidationError(
                "invalid_reference", "source binding references must be positive"
            )
        _repository_path(self.path_prefix, "path_prefix")


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    workspace_id: int
    source_kind: str
    source_ref: str
    observation_kind: str
    structured_payload: Mapping[str, Any]
    content_hash: str
    root_provenance_id: str
    source_family: str
    trust_class: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.workspace_id < 1:
            raise DomainValidationError("invalid_reference", "workspace must be positive")
        for field_name in (
            "source_kind",
            "source_ref",
            "observation_kind",
            "root_provenance_id",
            "source_family",
            "trust_class",
        ):
            _required(getattr(self, field_name), field_name)
        if not _SHA256.fullmatch(self.content_hash):
            raise DomainValidationError("invalid_content_hash", "content hash must be SHA-256")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise DomainValidationError("invalid_timestamp", "observed_at must include timezone")
        object.__setattr__(self, "structured_payload", _freeze(self.structured_payload))


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    workspace_id: int
    stable_key: str
    resolution_kind: ResolutionKind
    status: ResolutionStatus
    resolved_payload: Mapping[str, Any]
    observation_refs: tuple[int, ...]
    annotation_refs: tuple[int, ...]
    root_provenance_refs: tuple[str, ...]
    validator_version: str
    resolution_hash: str

    def __post_init__(self) -> None:
        if self.workspace_id < 1:
            raise DomainValidationError("invalid_reference", "workspace must be positive")
        _stable_key(self.stable_key)
        _unique_nonempty(self.observation_refs, "observation_refs")
        if self.status is ResolutionStatus.VERIFIED and len(set(self.root_provenance_refs)) < 2:
            raise DomainValidationError(
                "insufficient_provenance", "verified identity requires two provenance roots"
            )
        _required(self.validator_version, "validator_version")
        if not _SHA256.fullmatch(self.resolution_hash):
            raise DomainValidationError(
                "invalid_resolution_hash", "resolution hash must be SHA-256"
            )
        object.__setattr__(self, "resolved_payload", _freeze(self.resolved_payload))


@dataclass(frozen=True, slots=True)
class ResourceGraphRevision:
    workspace_id: int
    revision: int
    resolution_ids: tuple[int, ...]
    parent_revision_id: int | None = None

    def __post_init__(self) -> None:
        if min(self.workspace_id, self.revision) < 1:
            raise DomainValidationError("invalid_revision", "graph references must be positive")
        _unique_nonempty(self.resolution_ids, "resolution_ids")
        if self.parent_revision_id is not None and self.parent_revision_id < 1:
            raise DomainValidationError("invalid_reference", "parent revision must be positive")


@dataclass(frozen=True, slots=True)
class EvidenceAccessScope:
    connector_id: int
    allowed_languages: tuple[NativeLanguage, ...]
    scope_config: Mapping[str, Any]
    schema_catalog_revision: int
    read_policy_revision: int
    execution_budget_policy: Mapping[str, Any]
    normalization_policy_revision: int
    revision: int = 1

    def __post_init__(self) -> None:
        if (
            min(
                self.connector_id,
                self.schema_catalog_revision,
                self.read_policy_revision,
                self.normalization_policy_revision,
                self.revision,
            )
            < 1
        ):
            raise DomainValidationError("invalid_reference", "scope revisions must be positive")
        _unique_nonempty(self.allowed_languages, "allowed_languages")
        budget = ExecutionBudgetPolicy.from_mapping(self.execution_budget_policy)
        object.__setattr__(self, "scope_config", _freeze(self.scope_config))
        object.__setattr__(self, "execution_budget_policy", _freeze(budget.as_dict()))


@dataclass(frozen=True, slots=True)
class EvidenceConnector:
    workspace_id: int
    name: str
    kind: str
    kind_version: int
    instance_revision: int
    capabilities: tuple[str, ...]
    verification_status: HealthState
    state: LifecycleState = LifecycleState.ACTIVE

    def __post_init__(self) -> None:
        if min(self.workspace_id, self.kind_version, self.instance_revision) < 1:
            raise DomainValidationError("invalid_reference", "connector revisions must be positive")
        _required(self.name, "name")
        _stable_key(self.kind, "kind")
        _unique_nonempty(self.capabilities, "capabilities")


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    investigation_id: int
    artifact_kind: str
    content_hash: str
    provenance: Mapping[str, Any]
    evidence_class: str
    archived_at: datetime

    def __post_init__(self) -> None:
        if self.investigation_id < 1:
            raise DomainValidationError("invalid_reference", "investigation must be positive")
        _required(self.artifact_kind, "artifact_kind")
        _required(self.evidence_class, "evidence_class")
        if not _SHA256.fullmatch(self.content_hash):
            raise DomainValidationError("invalid_content_hash", "content hash must be SHA-256")
        if self.archived_at.tzinfo is None or self.archived_at.utcoffset() is None:
            raise DomainValidationError("invalid_timestamp", "archived_at must include timezone")
        object.__setattr__(self, "provenance", _freeze(self.provenance))


@dataclass(frozen=True, slots=True)
class ObservedRelation:
    investigation_id: int
    source_entity_id: int
    target_entity_id: int
    kind: RelationKind
    evidence_refs: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if min(self.investigation_id, self.source_entity_id, self.target_entity_id) < 1:
            raise DomainValidationError("invalid_reference", "relation references must be positive")
        if self.source_entity_id == self.target_entity_id:
            raise DomainValidationError("invalid_relation", "relation endpoints must differ")
        if self.kind in EVIDENCE_REQUIRED_RELATIONS and not self.evidence_refs:
            raise DomainValidationError(
                "missing_relation_evidence", f"{self.kind.value} requires explicit evidence"
            )
