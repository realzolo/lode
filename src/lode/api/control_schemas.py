"""Strict current control-plane request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExecutionClassValue = Literal["latency_optimized", "reasoning_optimized"]
ModelRoleValue = Literal["planner", "native_query", "synthesizer", "verifier", "context_compactor"]


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _StrictPatch(_StrictInput):
    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        invalid = sorted(
            field
            for field in self.model_fields_set
            if getattr(self, field) is None and field not in self.nullable_fields
        )
        if invalid:
            raise ValueError(f"patch fields must not be null: {', '.join(invalid)}")
        return self


class _ORMOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _ModelSelection(_StrictInput):
    model_ids: tuple[str, ...] = Field(max_length=100)
    manual_model_ids: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def valid_model_selection(self):
        if len(self.model_ids) != len(set(self.model_ids)):
            raise ValueError("model IDs must be unique")
        if len(self.manual_model_ids) != len(set(self.manual_model_ids)):
            raise ValueError("manual model IDs must be unique")
        if not set(self.manual_model_ids).issubset(self.model_ids):
            raise ValueError("manual model IDs must be selected")
        if any(not value or value != value.strip() for value in self.model_ids):
            raise ValueError("model IDs must be trimmed and nonempty")
        return self


class ProviderModelDiscoveryInput(_StrictInput):
    base_url: str = Field(min_length=1, max_length=2_000)
    credential: str = Field(min_length=1, max_length=8_000)
    organization_ref: str | None = Field(default=None, max_length=500)
    project_ref: str | None = Field(default=None, max_length=500)


class ProviderAccountCreate(ProviderModelDiscoveryInput, _ModelSelection):
    name: str = Field(min_length=1, max_length=200)
    model_ids: tuple[str, ...] = Field(min_length=1, max_length=100)


class ProviderAccountPatch(_StrictPatch):
    nullable_fields = frozenset({"organization_ref", "project_ref"})

    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=2_000)
    credential: str | None = Field(default=None, min_length=1, max_length=8_000)
    organization_ref: str | None = Field(default=None, max_length=500)
    project_ref: str | None = Field(default=None, max_length=500)
    model_ids: tuple[str, ...] | None = Field(default=None, max_length=100)
    manual_model_ids: tuple[str, ...] | None = Field(default=None, max_length=100)
    state: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def valid_model_selection(self):
        if self.model_ids is None:
            if self.manual_model_ids is not None:
                raise ValueError("manual model IDs require model IDs")
            return self
        selection = _ModelSelection(
            model_ids=self.model_ids,
            manual_model_ids=self.manual_model_ids or (),
        )
        self.model_ids = selection.model_ids
        self.manual_model_ids = selection.manual_model_ids
        return self


class ProviderAccountModelSelection(_ModelSelection):
    pass


class ProviderAccountModelOut(_ORMOutput):
    id: int
    provider_account_id: int
    provider_model_id: str
    display_name: str
    capabilities: dict[str, bool]
    discovery_state: Literal["synced", "manual", "missing"]
    availability_state: Literal["untested", "healthy", "unavailable"]
    health_checked_at: datetime | None
    state: Literal["active", "disabled"]
    revision: int
    created_at: datetime
    updated_at: datetime


class ProviderModelDiscoveryOut(_StrictInput):
    provider_model_id: str
    display_name: str


class ProviderAccountOut(_ORMOutput):
    id: int
    name: str
    provider_kind: Literal["openai_compatible"]
    base_url: str
    organization_ref: str | None
    project_ref: str | None
    state: str
    verification_status: str
    verified_at: datetime | None
    models: list[ProviderAccountModelOut]
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkspaceCreate(_StrictInput):
    name: str = Field(min_length=1, max_length=200)
    ingestion_topic: str = Field(min_length=1, max_length=500)

    @field_validator("name", "ingestion_topic")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class WorkspaceOut(_ORMOutput):
    id: int
    name: str
    ingestion_topic: str
    model_policy_revision_id: int | None
    investigation_policy_revision_id: int | None
    ingestion_state: str
    ingestion_version: int
    ingestion_start_position: str | None
    ingestion_started_at: datetime | None
    ingestion_paused_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IngestionStart(_StrictInput):
    start_position: Literal["earliest", "latest"]


class ModelBindingInput(_StrictInput):
    provider_account_model_id: int = Field(gt=0)
    execution_classes: tuple[ExecutionClassValue, ...] = Field(min_length=1)
    allowed_roles: tuple[ModelRoleValue, ...] = Field(min_length=1)
    priority: int = Field(default=0, ge=0)
    max_calls: int = Field(gt=0)
    max_cost_per_call: float = Field(ge=0)
    timeout_ms: int = Field(gt=0, le=600_000)
    allowed_data_classes: tuple[str, ...] = Field(min_length=1)
    max_context_utilization: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def unique_values(self):
        for values in (
            self.execution_classes,
            self.allowed_roles,
            self.allowed_data_classes,
        ):
            if len(values) != len(set(values)):
                raise ValueError("binding list values must be unique")
        return self


class ModelBindingPatch(_StrictPatch):
    execution_classes: tuple[ExecutionClassValue, ...] | None = Field(default=None, min_length=1)
    allowed_roles: tuple[ModelRoleValue, ...] | None = Field(default=None, min_length=1)
    priority: int | None = Field(default=None, ge=0)
    max_calls: int | None = Field(default=None, gt=0)
    max_cost_per_call: float | None = Field(default=None, ge=0)
    timeout_ms: int | None = Field(default=None, gt=0, le=600_000)
    allowed_data_classes: tuple[str, ...] | None = Field(default=None, min_length=1)
    max_context_utilization: float | None = Field(default=None, gt=0, lt=1)
    state: Literal["active", "disabled"] | None = None


class ModelBindingOut(_ORMOutput):
    id: int
    workspace_id: int
    provider_account_model_id: int
    execution_classes: list[str]
    allowed_roles: list[str]
    priority: int
    max_calls: int
    max_cost_per_call: float
    timeout_ms: int
    allowed_data_classes: list[str]
    max_context_utilization: float
    state: str
    revision: int
    created_at: datetime
    updated_at: datetime


class ModelPolicyInput(_StrictInput):
    eligible_binding_ids: tuple[int, ...] = Field(min_length=1)
    role_policies: dict[str, Any]
    verifier_policy: dict[str, Any] = Field(default_factory=dict)
    pinned_evidence_kinds: tuple[str, ...] = Field(min_length=1)
    compression_levels: tuple[str, ...] = Field(min_length=1)
    minimum_output_tokens: int = Field(gt=0)
    provider_safety_margin_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def unique_values(self):
        for values in (
            self.eligible_binding_ids,
            self.pinned_evidence_kinds,
            self.compression_levels,
        ):
            if len(values) != len(set(values)):
                raise ValueError("model policy list values must be unique")
        return self


class ModelPolicyOut(_ORMOutput):
    id: int
    workspace_id: int
    eligible_bindings: list[dict[str, int]]
    role_policies: dict[str, Any]
    context_policy_revision_id: int
    verifier_policy: dict[str, Any]
    revision: int
    created_at: datetime


class InvestigationPolicyPut(_StrictInput):
    profile: Literal["fast", "balanced", "deep"]


class InvestigationPolicyOut(_ORMOutput):
    id: int
    workspace_id: int
    profile: Literal["fast", "balanced", "deep"]
    max_evidence_steps: int
    max_model_calls: int
    max_native_reads: int
    max_output_bytes: int
    max_cost: float
    timeout_seconds: int
    window_before_seconds: int
    window_after_seconds: int
    revision: int
    created_at: datetime


class PlatformSettingsUpdate(_StrictInput):
    ai_output_language: Literal["en", "zh"]
    expected_revision: int = Field(gt=0)


class PlatformSettingsOut(_ORMOutput):
    ai_output_language: Literal["en", "zh"]
    revision: int
    updated_at: datetime
    supported_languages: list[Literal["en", "zh"]]


class RepositoryBind(_StrictInput):
    repository_id: int = Field(gt=0)
    role: Literal["runtime_source", "shared_library", "infrastructure", "documentation"]
    priority: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=2_000)


class LocalRepositoryCreate(_StrictInput):
    name: str = Field(min_length=1, max_length=200)
    repo_url: str = Field(min_length=1, max_length=2_000)
    repo_type: str = Field(default="other", max_length=100)
    default_branch: str = Field(default="main", min_length=1, max_length=200)
    credential_id: int | None = Field(default=None, gt=0)
    role: Literal["runtime_source", "shared_library", "infrastructure", "documentation"]
    priority: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=2_000)


class RepositoryBindingPatch(_StrictPatch):
    role: Literal["runtime_source", "shared_library", "infrastructure", "documentation"] | None = (
        None
    )
    priority: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2_000)
    state: Literal["active", "disabled"] | None = None


class RepositoryBindingOut(_StrictInput):
    id: int
    workspace_id: int
    repository_id: int
    name: str
    repo_url: str
    repo_type: str
    default_branch: str
    role: str
    priority: int
    description: str
    state: str
    revision: int


class ConnectorCreate(_StrictInput):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "loki", "elasticsearch", "opensearch", "postgresql", "mysql", "https", "command_runner"
    ]
    config: dict[str, Any]
    secrets: dict[str, str] = Field(default_factory=dict)
    scope_config: dict[str, Any]
    schema_catalog: dict[str, Any] = Field(default_factory=dict)
    execution_budget_policy: dict[str, Any]


class ConnectorPatch(_StrictPatch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None
    scope_config: dict[str, Any] | None = None
    schema_catalog: dict[str, Any] | None = None
    execution_budget_policy: dict[str, Any] | None = None
    state: Literal["active", "disabled"] | None = None


class ConnectorOut(_StrictInput):
    id: int
    workspace_id: int
    name: str
    kind: str
    kind_version: int
    config: dict[str, Any]
    instance_revision: int
    state: str
    verification_status: str
    verified_at: datetime | None
    last_error: str | None
    capabilities: list[str]
    last_introspected_at: datetime | None
    configured_secret_fields: list[str]
