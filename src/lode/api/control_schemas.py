"""Strict current control-plane request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lode.api.types import EntityId

ExecutionClassValue = Literal["latency_optimized", "reasoning_optimized"]
ModelRoleValue = Literal["planner", "native_query", "synthesizer", "verifier", "context_compactor"]
ProviderKindValue = Literal["openai", "anthropic"]
ProviderProtocolValue = Literal[
    "openai.responses.v1", "openai.chat_completions.v1", "anthropic.messages.v1"
]


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


class ProviderModelSelectionItem(_StrictInput):
    provider_model_id: str = Field(min_length=1, max_length=200)
    source: Literal["discovered", "manual"]

    @field_validator("provider_model_id")
    @classmethod
    def trimmed_model_id(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("model ID must be trimmed")
        return value


class _ModelSelection(_StrictInput):
    models: tuple[ProviderModelSelectionItem, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def valid_model_selection(self):
        model_ids = [item.provider_model_id for item in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("model IDs must be unique")
        return self


class ProviderAccountConnectionInput(_StrictInput):
    provider_kind: ProviderKindValue
    protocol_id: ProviderProtocolValue
    base_url: str = Field(min_length=1, max_length=2_000)
    api_key: str = Field(min_length=1, max_length=8_000)

    @model_validator(mode="after")
    def valid_provider_protocol(self):
        if self.provider_kind == "openai" and not self.protocol_id.startswith("openai."):
            raise ValueError("OpenAI accounts require an OpenAI protocol")
        if self.provider_kind == "anthropic" and self.protocol_id != "anthropic.messages.v1":
            raise ValueError("Anthropic accounts require the Anthropic Messages protocol")
        return self


class ProviderAccountCreate(ProviderAccountConnectionInput, _ModelSelection):
    name: str = Field(min_length=1, max_length=200)
    models: tuple[ProviderModelSelectionItem, ...] = Field(min_length=1, max_length=100)


class ProviderAccountPatch(_StrictPatch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    provider_kind: ProviderKindValue | None = None
    protocol_id: ProviderProtocolValue | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2_000)
    api_key: str | None = Field(default=None, min_length=1, max_length=8_000)
    models: tuple[ProviderModelSelectionItem, ...] | None = Field(default=None, max_length=100)
    state: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def valid_model_selection(self):
        if self.models is None:
            return self
        selection = _ModelSelection(models=self.models)
        self.models = selection.models
        return self


class ProviderAccountModelSelection(_ModelSelection):
    pass


class ProviderModelCatalogOut(_StrictInput):
    provider_kind: ProviderKindValue
    provider_model_id: str
    display_name: str
    context_window_tokens: int
    max_output_tokens: int
    capabilities: dict[str, bool]
    protocol_ids: tuple[str, ...]
    catalog_revision: str
    source_url: str
    reviewed_at: str


class ProviderModelDiscoveryOut(_StrictInput):
    catalog_revision: str
    available_model_ids: tuple[str, ...]
    unsupported_model_ids: tuple[str, ...]


class ProviderAccountModelOut(_ORMOutput):
    id: EntityId
    provider_account_id: EntityId
    provider_model_id: str
    display_name: str
    capabilities: dict[str, bool]
    discovery_state: Literal["discovered", "manual", "missing"]
    availability_state: Literal["untested", "healthy", "unavailable"]
    health_checked_at: datetime | None
    state: Literal["active", "disabled"]
    revision: int
    created_at: datetime
    updated_at: datetime


class ProviderAccountOut(_ORMOutput):
    id: EntityId
    name: str
    provider_kind: ProviderKindValue
    protocol_id: ProviderProtocolValue
    base_url: str
    state: str
    verification_status: str
    verified_at: datetime | None
    models: list[ProviderAccountModelOut]
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkspaceCreate(_StrictInput):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1_000)
    ingestion_topic: str = Field(min_length=1, max_length=500)

    @field_validator("name", "ingestion_topic")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class WorkspacePatch(_StrictPatch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    ingestion_topic: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("name", "ingestion_topic")
    @classmethod
    def strip_nonblank_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class WorkspaceOut(_ORMOutput):
    id: EntityId
    name: str
    description: str
    ingestion_topic: str
    model_policy_revision_id: EntityId | None
    architecture_context_revision_id: EntityId | None
    ingestion_state: str
    ingestion_version: int
    ingestion_start_position: str | None
    ingestion_started_at: datetime | None
    ingestion_paused_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkspaceReadinessCheckOut(_StrictInput):
    code: Literal[
        "kafka_topic",
        "model_policy",
        "repositories",
        "evidence_connectors",
        "architecture_context",
    ]
    outcome: Literal["passed", "blocked", "warning"]
    details: dict[str, Any] = Field(default_factory=dict)


class WorkspaceIngestionRuntimeOut(_StrictInput):
    observed_state: Literal["idle", "starting", "listening", "paused", "error"]
    observed_version: int = Field(ge=0)
    consumer_id: str | None
    assigned_partitions: int = Field(ge=0)
    backlog: int | None
    last_heartbeat_at: datetime | None
    last_error: str | None


class WorkspaceReadinessOut(_StrictInput):
    workspace_id: EntityId
    can_start: bool
    checks: tuple[WorkspaceReadinessCheckOut, ...]
    runtime: WorkspaceIngestionRuntimeOut


ArchitectureContextKind = Literal[
    "system_purpose",
    "architecture",
    "critical_flow",
    "dependency",
    "operational_convention",
]


class WorkspaceArchitectureContextEntry(_StrictInput):
    kind: ArchitectureContextKind
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4_000)

    @field_validator("title", "content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("architecture context text must not be blank")
        return value


class WorkspaceArchitectureContextPut(_StrictInput):
    entries: tuple[WorkspaceArchitectureContextEntry, ...] = Field(max_length=20)

    @model_validator(mode="after")
    def bounded_total_content(self):
        if sum(len(item.title) + len(item.content) for item in self.entries) > 20_000:
            raise ValueError("architecture context exceeds the total text limit")
        return self


class WorkspaceArchitectureContextOut(_StrictInput):
    id: EntityId
    workspace_id: EntityId
    entries: tuple[WorkspaceArchitectureContextEntry, ...]
    revision: int = Field(gt=0)
    created_at: datetime


class IngestionStart(_StrictInput):
    start_position: Literal["earliest", "latest"]


class ModelBindingInput(_StrictInput):
    provider_account_model_id: EntityId = Field(gt=0)
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
    id: EntityId
    workspace_id: EntityId
    provider_account_model_id: EntityId
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
    eligible_binding_ids: tuple[EntityId, ...] = Field(min_length=1)
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
    id: EntityId
    workspace_id: EntityId
    eligible_bindings: list[dict[str, int]]
    role_policies: dict[str, Any]
    context_policy_revision_id: EntityId
    verifier_policy: dict[str, Any]
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


class GitAccountCreate(_StrictInput):
    adapter_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    api_url: str | None = Field(default=None, max_length=2_000)
    access_token: str = Field(min_length=1, max_length=8_000)


class GitAccountPatch(_StrictPatch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    state: Literal["active", "disabled"] | None = None


class GitAccountTokenRotate(_StrictInput):
    access_token: str = Field(min_length=1, max_length=8_000)


class GitAccountOut(_ORMOutput):
    id: EntityId
    adapter_id: str
    api_url: str
    name: str
    external_account_id: str
    external_account_login: str
    account_url: str
    state: Literal["active", "disabled", "revoked"]
    verification_status: Literal["untested", "healthy", "unavailable"]
    verified_at: datetime | None
    last_synced_at: datetime | None
    last_error: str | None
    repository_count: int
    revision: int
    created_at: datetime
    updated_at: datetime


class GitAccountRepositoryOut(_StrictInput):
    repository_id: EntityId
    provider_kind: Literal["github", "gitlab", "gitee"]
    full_name: str
    repo_url: str
    web_url: str
    default_branch: str
    visibility: Literal["public", "private", "internal"]
    archived: bool


class RepositoryBind(_StrictInput):
    account_connection_id: EntityId = Field(gt=0)
    repository_id: EntityId = Field(gt=0)
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
    id: EntityId
    workspace_id: EntityId
    repository_id: EntityId
    account_connection_id: EntityId
    account_name: str
    external_account_login: str
    provider_kind: Literal["github", "gitlab", "gitee"]
    name: str
    full_name: str
    repo_url: str
    web_url: str
    repo_type: str
    default_branch: str
    role: str
    priority: int
    description: str
    state: str
    revision: int


class RepositoryAnalysisJobOut(_ORMOutput):
    id: EntityId
    workspace_id: EntityId
    requested_binding_ids: list[EntityId]
    state: Literal["queued", "running", "succeeded", "failed"]
    attempt: int
    source_revisions: dict[str, str]
    graph_revision_id: EntityId | None
    scanned_file_count: int
    issue_count: int
    failure_code: Literal[
        "repository_access_unavailable",
        "repository_checkout_failed",
        "repository_manifest_invalid",
        "repository_analysis_failed",
    ] | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LokiConditionInput(_StrictInput):
    kind: Literal["condition"] = "condition"
    label: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    operator: Literal["equals", "not_equals", "any_of", "not_any_of"]
    values: tuple[str, ...] = Field(min_length=1, max_length=20)


class LokiConditionGroupInput(_StrictInput):
    kind: Literal["group"] = "group"
    combinator: Literal["all", "any"]
    items: tuple["LokiConditionInput | LokiConditionGroupInput", ...] = Field(min_length=1)


LokiConditionGroupInput.model_rebuild()


class ConnectorCreate(_StrictInput):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["loki", "elasticsearch", "opensearch", "postgresql", "mysql", "https"]
    endpoint: str | None = Field(default=None, min_length=1, max_length=1_000)
    authentication: Literal["none", "bearer_token", "api_key", "basic"] = "none"
    credential: str | None = Field(default=None, min_length=1, max_length=8_000)
    credential_username: str | None = Field(default=None, min_length=1, max_length=200)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=200)
    root_filter: LokiConditionGroupInput | None = None
    allowed_indices: tuple[str, ...] = Field(default=(), max_length=500)
    verification_path: str = Field(default="/health", min_length=1, max_length=1_000)
    safe_read_path: str | None = Field(default=None, min_length=1, max_length=1_000)
    safe_read_content_type: str = Field(default="application/json", min_length=3, max_length=200)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65_535)
    database: str | None = Field(default=None, min_length=1, max_length=64)
    database_username: str | None = Field(default=None, min_length=1, max_length=64)
    database_password: str | None = Field(default=None, min_length=1, max_length=8_000)

    @model_validator(mode="after")
    def valid_kind_specific_form(self):
        if self.kind in {"loki", "elasticsearch", "opensearch", "https"} and not self.endpoint:
            raise ValueError("an HTTPS endpoint is required")
        if self.kind == "loki" and self.root_filter is None:
            raise ValueError("Loki requires a root label filter")
        if self.kind == "loki" and self.root_filter is not None:
            from lode.evidence_access.loki_scope import normalize_loki_filter

            normalize_loki_filter(self.root_filter.model_dump())
        if self.kind in {"elasticsearch", "opensearch"} and not self.allowed_indices:
            raise ValueError("search connectors require at least one allowed index")
        if self.kind == "https" and not self.safe_read_path:
            raise ValueError("HTTPS connectors require one allowed read path")
        if self.kind in {"postgresql", "mysql"} and not all(
            (self.host, self.database, self.database_username, self.database_password)
        ):
            raise ValueError("database connectors require database connection fields")
        if self.kind == "loki" and self.authentication not in {"none", "bearer_token"}:
            raise ValueError("Loki supports only bearer-token authentication")
        if self.kind in {"elasticsearch", "opensearch", "https"} and self.authentication == "none":
            raise ValueError("this connector requires an authentication method")
        if self.kind in {"loki", "elasticsearch", "opensearch", "https"} and self.authentication == "basic" and not self.credential_username:
            raise ValueError("basic authentication requires a username")
        if self.kind in {"loki", "elasticsearch", "opensearch", "https"} and self.authentication != "none" and not self.credential:
            raise ValueError("the selected authentication method requires a credential")
        if self.kind in {"loki", "elasticsearch", "opensearch", "https"} and self.authentication == "none" and self.credential is not None:
            raise ValueError("a credential requires an authentication method")
        return self


class ConnectorOut(_StrictInput):
    id: EntityId
    workspace_id: EntityId
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
