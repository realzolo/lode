"""Strict current control-plane request and response schemas."""

from __future__ import annotations

import re
import ssl
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lode.api.types import EntityId
from lode.domain.types import ModelDataClass

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
    allowed_data_classes: tuple[ModelDataClass, ...] = Field(min_length=1)
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
    allowed_data_classes: tuple[ModelDataClass, ...] | None = Field(default=None, min_length=1)
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
    allowed_data_classes: list[ModelDataClass]
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


class GitBranchOut(_StrictInput):
    name: str
    is_default: bool = False


class GitBranchPageOut(_StrictInput):
    items: list[GitBranchOut]
    next_cursor: str | None = None


class RepositoryBind(_StrictInput):
    account_connection_id: EntityId = Field(gt=0)
    repository_id: EntityId = Field(gt=0)
    role: Literal["runtime_source", "shared_library", "infrastructure", "documentation"]
    branch_mode: Literal["default", "branch"] = "default"
    branch_name: str | None = Field(default=None, min_length=1, max_length=255)
    priority: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def valid_branch_selection(self):
        if self.branch_name is not None and self.branch_name != self.branch_name.strip():
            raise ValueError("branch name must be trimmed")
        if self.branch_mode == "default" and self.branch_name is not None:
            raise ValueError("default branch mode must not include a branch name")
        if self.branch_mode == "branch" and not self.branch_name:
            raise ValueError("fixed branch mode requires a branch name")
        return self


class RepositoryBindingPatch(_StrictPatch):
    nullable_fields: ClassVar[frozenset[str]] = frozenset({"branch_name"})
    expected_revision: int = Field(gt=0)
    role: Literal["runtime_source", "shared_library", "infrastructure", "documentation"] | None = (
        None
    )
    branch_mode: Literal["default", "branch"] | None = None
    branch_name: str | None = Field(default=None, min_length=1, max_length=255)
    priority: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2_000)
    state: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def valid_branch_selection(self):
        if self.branch_name is not None and self.branch_name != self.branch_name.strip():
            raise ValueError("branch name must be trimmed")
        if self.branch_mode == "default" and "branch_name" in self.model_fields_set:
            if self.branch_name is not None:
                raise ValueError("default branch mode must not include a branch name")
        if self.branch_mode == "branch" and "branch_name" in self.model_fields_set:
            if not self.branch_name:
                raise ValueError("fixed branch mode requires a branch name")
        return self


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
    branch_mode: Literal["default", "branch"]
    branch_name: str | None
    effective_branch: str
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
    result_status: Literal["pending", "clean", "warnings", "failed"]
    is_current: bool = False
    attempt: int
    source_branches: dict[str, str]
    source_revisions: dict[str, str]
    graph_revision_id: EntityId | None
    scanned_file_count: int
    issue_count: int
    failure_code: Literal[
        "repository_access_unavailable",
        "repository_branch_unavailable",
        "repository_checkout_failed",
        "repository_scan_limit_exceeded",
        "repository_analysis_failed",
    ] | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RepositoryAnalysisIssueOut(_ORMOutput):
    id: EntityId
    repository_analysis_job_id: EntityId
    repository_binding_id: EntityId | None
    ordinal: int
    severity: Literal["warning", "error"]
    code: str
    path: str | None
    detail: str
    created_at: datetime


class RepositoryAnalysisIssuePageOut(_StrictInput):
    items: list[RepositoryAnalysisIssueOut]
    next_cursor: int | None = None


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


_SEARCH_INDEX = r"^[a-z0-9][a-z0-9_.-]{0,254}$"
_POSTGRES_SCHEMA = r"^[A-Za-z_][A-Za-z0-9_$-]{0,62}$"
_SYSTEM_POSTGRES_SCHEMAS = frozenset({"information_schema", "pg_catalog"})


def _validate_connector_authentication(
    authentication: str, credential: str | None, credential_username: str | None
) -> None:
    if authentication == "basic" and not credential_username:
        raise ValueError("basic authentication requires a username")
    if authentication != "basic" and credential_username is not None:
        raise ValueError("a username is valid only for basic authentication")
    if not credential:
        raise ValueError("the selected authentication method requires a credential")


def _validate_database_connection_name(value: str) -> str:
    if value != value.strip() or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError(
            "database connection names must not contain surrounding whitespace "
            "or control characters"
        )
    return value


def _validate_database_ca_certificate(value: str | None) -> str | None:
    if value is None:
        return None
    if "PRIVATE KEY-----" in value.upper():
        raise ValueError("database CA configuration must not contain a private key")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=value)
    except (ssl.SSLError, ValueError) as exc:
        raise ValueError(
            "database CA certificate must contain valid PEM-encoded certificate data"
        ) from exc
    return value


def _validate_database_tls_configuration(
    tls_mode: str, ca_certificate_pem: str | None
) -> None:
    if tls_mode == "require" and ca_certificate_pem is not None:
        raise ValueError(
            "database CA certificate is valid only with full TLS verification"
        )


class _ConnectorCreateBase(_StrictInput):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("connector name must not be blank")
        return value


class LokiConnectorCreate(_ConnectorCreateBase):
    kind: Literal["loki"]
    endpoint: str = Field(min_length=1, max_length=1_000)
    authentication: Literal["none", "bearer_token"] = "none"
    credential: str | None = Field(default=None, min_length=1, max_length=8_000)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=200)
    root_filter: LokiConditionGroupInput

    @model_validator(mode="after")
    def valid_loki_form(self):
        if self.authentication == "bearer_token" and not self.credential:
            raise ValueError("bearer-token authentication requires a credential")
        if self.authentication == "none" and self.credential is not None:
            raise ValueError("a credential requires an authentication method")
        from lode.evidence_access.loki_scope import normalize_loki_filter

        normalize_loki_filter(self.root_filter.model_dump())
        return self


class SearchConnectorCreate(_ConnectorCreateBase):
    kind: Literal["elasticsearch", "opensearch"]
    endpoint: str = Field(min_length=1, max_length=1_000)
    authentication: Literal["bearer_token", "api_key", "basic"]
    credential: str = Field(min_length=1, max_length=8_000)
    credential_username: str | None = Field(default=None, min_length=1, max_length=200)
    allowed_indices: tuple[str, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid_search_form(self):
        _validate_connector_authentication(
            self.authentication, self.credential, self.credential_username
        )
        if len(self.allowed_indices) != len(set(self.allowed_indices)):
            raise ValueError("allowed indices must be unique")
        for index in self.allowed_indices:
            if (
                re.fullmatch(_SEARCH_INDEX, index) is None
                or index.startswith(".")
                or ".." in index
                or index in {"_all", "all"}
                or any(character in index for character in "*,/")
            ):
                raise ValueError("allowed indices must be exact non-reserved index names")
        return self


class PostgreSQLConnectorCreate(_ConnectorCreateBase):
    kind: Literal["postgresql"]
    host: str = Field(min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65_535)
    database: str = Field(min_length=1, max_length=63)
    database_username: str = Field(min_length=1, max_length=63)
    database_password: str = Field(min_length=1, max_length=8_000)
    tls_mode: Literal["verify_full", "require"]
    ca_certificate_pem: str | None = Field(default=None, min_length=1, max_length=64_000)
    allowed_schemas: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator("database", "database_username")
    @classmethod
    def valid_connection_name(cls, value: str) -> str:
        return _validate_database_connection_name(value)

    @field_validator("ca_certificate_pem")
    @classmethod
    def valid_ca_certificate(cls, value: str | None) -> str | None:
        return _validate_database_ca_certificate(value)

    @model_validator(mode="after")
    def valid_allowed_schemas(self):
        _validate_database_tls_configuration(
            self.tls_mode, self.ca_certificate_pem
        )
        if len(self.allowed_schemas) != len(set(self.allowed_schemas)):
            raise ValueError("PostgreSQL allowed schemas must be unique")
        if any(
            re.fullmatch(_POSTGRES_SCHEMA, schema) is None
            or schema.lower() in _SYSTEM_POSTGRES_SCHEMAS
            or schema.lower().startswith("pg_")
            for schema in self.allowed_schemas
        ):
            raise ValueError("PostgreSQL allowed schemas must be exact non-system names")
        return self


class MySQLConnectorCreate(_ConnectorCreateBase):
    kind: Literal["mysql"]
    host: str = Field(min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65_535)
    database: str = Field(min_length=1, max_length=64)
    database_username: str = Field(min_length=1, max_length=64)
    database_password: str = Field(min_length=1, max_length=8_000)
    tls_mode: Literal["verify_full", "require"]
    ca_certificate_pem: str | None = Field(default=None, min_length=1, max_length=64_000)

    @field_validator("database", "database_username")
    @classmethod
    def valid_connection_name(cls, value: str) -> str:
        return _validate_database_connection_name(value)

    @field_validator("ca_certificate_pem")
    @classmethod
    def valid_ca_certificate(cls, value: str | None) -> str | None:
        return _validate_database_ca_certificate(value)

    @model_validator(mode="after")
    def valid_tls_configuration(self):
        _validate_database_tls_configuration(
            self.tls_mode, self.ca_certificate_pem
        )
        return self


class HTTPSConnectorCreate(_ConnectorCreateBase):
    kind: Literal["https"]
    endpoint: str = Field(min_length=1, max_length=1_000)
    authentication: Literal["bearer_token", "api_key", "basic"]
    credential: str = Field(min_length=1, max_length=8_000)
    credential_username: str | None = Field(default=None, min_length=1, max_length=200)
    verification_path: str | None = Field(default=None, min_length=1, max_length=1_000)
    safe_read_path: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def valid_https_form(self):
        _validate_connector_authentication(
            self.authentication, self.credential, self.credential_username
        )
        return self


ConnectorCreate: TypeAlias = Annotated[
    LokiConnectorCreate
    | SearchConnectorCreate
    | PostgreSQLConnectorCreate
    | MySQLConnectorCreate
    | HTTPSConnectorCreate,
    Field(discriminator="kind"),
]


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
