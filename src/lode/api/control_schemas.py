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


class GitProviderInstanceCreate(_StrictInput):
    kind: Literal["github", "gitlab", "gitee"]
    name: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2_000)
    api_url: str | None = Field(default=None, max_length=2_000)
    github_app_id: str | None = Field(default=None, max_length=200)
    github_app_private_key: str | None = Field(default=None, max_length=100_000)
    oauth_client_id: str | None = Field(default=None, max_length=2_000)
    oauth_client_secret: str | None = Field(default=None, max_length=8_000)
    oauth_redirect_uri: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def valid_native_auth(self):
        github_values = (self.github_app_id, self.github_app_private_key)
        oauth_values = (self.oauth_client_id, self.oauth_client_secret, self.oauth_redirect_uri)
        if self.kind == "github":
            if any(value is not None for value in oauth_values):
                raise ValueError("GitHub provider instances do not use OAuth client fields")
            if any(value is not None for value in github_values) and not all(github_values):
                raise ValueError("GitHub App ID and private key must be supplied together")
        else:
            if any(value is not None for value in github_values):
                raise ValueError("only GitHub provider instances accept GitHub App fields")
            if any(value is not None for value in oauth_values) and not all(oauth_values):
                raise ValueError("OAuth client ID, secret, and redirect URI must be supplied together")
        return self


class GitProviderInstanceOut(_ORMOutput):
    id: int
    kind: Literal["github", "gitlab", "gitee"]
    name: str
    base_url: str
    api_url: str
    state: Literal["active", "disabled"]
    verification_status: Literal["untested", "healthy", "unavailable"]
    verified_at: datetime | None
    last_error: str | None
    native_auth_available: bool
    native_auth_kind: Literal["github_app", "oauth"] | None
    revision: int
    created_at: datetime
    updated_at: datetime


class GitProviderInstancePatch(_StrictPatch):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    state: Literal["active", "disabled"] | None = None


class GitAccountManualCreate(_StrictInput):
    provider_instance_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    access_token: str = Field(min_length=1, max_length=8_000)


class GitHubAppConnectionCreate(_StrictInput):
    provider_instance_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    installation_id: str = Field(min_length=1, max_length=200)


class GitOAuthStart(_StrictInput):
    name: str = Field(min_length=1, max_length=200)


class GitOAuthStartOut(_StrictInput):
    authorization_url: str


class GitAccountConnectionOut(_ORMOutput):
    id: int
    provider_instance_id: int
    provider_kind: Literal["github", "gitlab", "gitee"]
    provider_name: str
    name: str
    auth_mode: Literal["github_app", "oauth", "access_token"]
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
    repository_id: int
    provider_kind: Literal["github", "gitlab", "gitee"]
    full_name: str
    repo_url: str
    web_url: str
    default_branch: str
    visibility: Literal["public", "private", "internal"]
    archived: bool


class WorkspaceGitAccountGrantCreate(_StrictInput):
    account_connection_id: int = Field(gt=0)
    repository_scope: Literal["selected", "all_visible"] = "selected"
    repository_ids: tuple[int, ...] = Field(default=(), max_length=1_000)

    @model_validator(mode="after")
    def selected_scope_requires_repositories(self):
        if len(self.repository_ids) != len(set(self.repository_ids)):
            raise ValueError("repository IDs must be unique")
        if self.repository_scope == "selected" and not self.repository_ids:
            raise ValueError("selected repository access requires at least one repository")
        return self


class WorkspaceGitAccountGrantOut(_ORMOutput):
    id: int
    workspace_id: int
    account_connection_id: int
    account_name: str
    provider_kind: Literal["github", "gitlab", "gitee"]
    external_account_login: str
    repository_scope: Literal["selected", "all_visible"]
    state: Literal["active", "disabled"]
    repository_count: int
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkspaceRepositoryCandidateOut(_StrictInput):
    entitlement_id: int
    repository_id: int
    provider_kind: Literal["github", "gitlab", "gitee"]
    full_name: str
    repo_url: str
    web_url: str
    default_branch: str
    visibility: Literal["public", "private", "internal"]
    archived: bool
    account_connection_id: int
    account_name: str


class RepositoryBind(_StrictInput):
    repository_entitlement_id: int = Field(gt=0)
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
    repository_entitlement_id: int
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


class ConnectorMatcherInput(_StrictInput):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    value: str = Field(min_length=1, max_length=1_000)


class SqlTableScopeInput(_StrictInput):
    table: str = Field(min_length=3, max_length=260, pattern=r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
    time_column: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    stable_order: tuple[str, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def stable_order_is_unique(self):
        if len(self.stable_order) != len(set(self.stable_order)):
            raise ValueError("stable order columns must be unique")
        return self


class ConnectorCreate(_StrictInput):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["loki", "elasticsearch", "opensearch", "postgresql", "mysql", "https"]
    endpoint: str | None = Field(default=None, min_length=1, max_length=1_000)
    authentication: Literal["none", "bearer_token", "api_key", "basic"] = "none"
    credential: str | None = Field(default=None, min_length=1, max_length=8_000)
    credential_username: str | None = Field(default=None, min_length=1, max_length=200)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=200)
    root_matchers: tuple[ConnectorMatcherInput, ...] = Field(default=(), max_length=32)
    allowed_indices: tuple[str, ...] = Field(default=(), max_length=500)
    verification_path: str = Field(default="/health", min_length=1, max_length=1_000)
    safe_read_path: str | None = Field(default=None, min_length=1, max_length=1_000)
    safe_read_content_type: str = Field(default="application/json", min_length=3, max_length=200)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65_535)
    database: str | None = Field(default=None, min_length=1, max_length=64)
    database_username: str | None = Field(default=None, min_length=1, max_length=64)
    database_password: str | None = Field(default=None, min_length=1, max_length=8_000)
    ca_certificate_pem: str | None = Field(default=None, min_length=1, max_length=100_000)
    allowed_tables: tuple[SqlTableScopeInput, ...] = Field(default=(), max_length=500)

    @model_validator(mode="after")
    def valid_kind_specific_form(self):
        if self.kind in {"loki", "elasticsearch", "opensearch", "https"} and not self.endpoint:
            raise ValueError("an HTTPS endpoint is required")
        if self.kind == "loki" and not self.root_matchers:
            raise ValueError("Loki requires at least one root label matcher")
        if self.kind in {"elasticsearch", "opensearch"} and not self.allowed_indices:
            raise ValueError("search connectors require at least one allowed index")
        if self.kind == "https" and not self.safe_read_path:
            raise ValueError("HTTPS connectors require one allowed read path")
        if self.kind in {"postgresql", "mysql"} and not all(
            (self.host, self.database, self.database_username, self.database_password, self.ca_certificate_pem)
        ):
            raise ValueError("database connectors require read-only database and TLS fields")
        if self.kind in {"postgresql", "mysql"} and not self.allowed_tables:
            raise ValueError("database connectors require at least one allowed table")
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
