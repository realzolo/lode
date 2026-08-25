"""API request/response schemas.

These are intentionally decoupled from the ORM models: they describe exactly
what the frontend needs (joined titles, latest levels, workflow steps) and
keep internal columns (ids, secrets, raw payloads in full) out of the wire.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lode.integration_policy import normalize_integration_config


class ApplicationOut(BaseModel):
    id: int
    name: str
    topic: str | None
    latest_level: str
    repo_count: int
    ingestion_state: Literal["draft", "active", "paused"]
    ingestion_observed_state: Literal["draft", "starting", "listening", "paused", "error"]
    ingestion_start_position: Literal["earliest", "latest"] | None = None
    my_perm: str | None = None
    created_at: datetime


class ApplicationDetailOut(BaseModel):
    id: int
    name: str
    topic: str | None
    model_config_id: int | None
    ingestion_state: Literal["draft", "active", "paused"]
    created_at: datetime
    repos: list[dict]
    descriptions: list[dict]
    db_sources: list[DbSourceListItem]
    integrations: list["ApplicationIntegrationOut"] = Field(default_factory=list)
    my_perm: str | None = None


class CreateApplicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# --- Application configuration (admin only) ----------------------------
#
# These power the per-application edit forms: Kafka topic binding, repository
# binding, read-only data sources, descriptions. The on-the-wire shapes are
# intentionally minimal — only what the Settings tabs need to read back into
# their forms. Server-side this is enforced via ``require_admin``.

class SetApplicationTopicIn(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=500)
    """Set / clear the Kafka topic for an application.

    Sending ``null`` (or omitting) detaches any current binding. Topics are
    globally unique across applications (DB constraint), so the operation is
    upsert-or-delete at the row level.
    """


class ApplicationTopicOut(BaseModel):
    application_id: int
    topic: str | None


class StartApplicationIngestionIn(BaseModel):
    start_position: Literal["earliest", "latest"]


class ApplicationIngestionStatusOut(BaseModel):
    application_id: int
    topic: str | None
    desired_state: Literal["draft", "active", "paused"]
    observed_state: Literal["draft", "starting", "listening", "paused", "error"]
    ingestion_version: int
    start_position: Literal["earliest", "latest"] | None
    assigned_partitions: int = 0
    backlog: int | None = None
    last_heartbeat_at: datetime | None = None
    last_error: str | None = None


class BindRepoIn(BaseModel):
    repo_id: int = Field(gt=0)
    description: str = Field(default="", max_length=2000)


class CreateApplicationRepoIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo_url: str = Field(min_length=1, max_length=2000)
    default_branch: str = Field(default="main", max_length=200)
    repo_type: str = Field(default="other", max_length=32)
    credential_id: int | None = Field(default=None, gt=0)
    description: str = Field(default="", max_length=2000)


class ApplicationRepoOut(BaseModel):
    id: int
    application_id: int
    repo_id: int
    repo_name: str
    repo_url: str
    repo_scope: str
    repo_type: str
    default_branch: str
    description: str


class SetApplicationModelIn(BaseModel):
    model_config_id: int | None = Field(default=None, gt=0)


class ApplicationModelOut(BaseModel):
    application_id: int
    model_config_id: int | None


class CreateDbSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    # Mode 1 (structured): enter connection fields directly. The DSN is built
    # at query time and the password lives in this DB row (acceptable for a
    # self-hosted admin console; prefer env:// for stricter deployments).
    host: str | None = Field(default=None, max_length=500)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=2000)
    # Mode 2 (secret ref): conn_secret_ref keeps real credentials out of the
    # row. Either this OR (host + database) must be supplied.
    conn_secret_ref: str | None = Field(default=None, max_length=1000)
    # All production data-source links verify the server certificate and name.
    sslmode: Literal["verify-full"] | None = None
    allowed_tables: list[str] = Field(min_length=1, max_length=100)
    # Operator-supplied extra column names to mask in results, on top of the
    # built-in heuristic hints.
    sensitive_columns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_connection(self) -> "CreateDbSourceIn":
        structured = bool(self.host) and bool(self.database)
        if not self.conn_secret_ref and not structured:
            raise ValueError(
                "provide either conn_secret_ref or both host and database"
            )
        if self.conn_secret_ref and structured:
            raise ValueError("conn_secret_ref and structured connection fields are mutually exclusive")
        if self.host and not self.database:
            raise ValueError("database is required when host is set")
        if self.database and not self.host:
            raise ValueError("host is required when database is set")
        if structured and self.sslmode != "verify-full":
            raise ValueError("structured data sources require sslmode=verify-full")
        return self

    @field_validator("conn_secret_ref")
    @classmethod
    def _require_environment_secret_reference(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"env://[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("conn_secret_ref must be an env://NAME reference")
        return value

    @field_validator("allowed_tables")
    @classmethod
    def _require_qualified_base_tables(cls, value: list[str]) -> list[str]:
        if any(table.count(".") != 1 for table in value):
            raise ValueError("approved tables must be schema-qualified base table names")
        return value


class DbSourceListItem(BaseModel):
    """Read shape for a data source as returned inside an application detail.

    A strict, explicit projection (not a loose ``dict``) so the frontend can
    rely on every field being present or explicitly ``None``. The raw password
    is never included; ``has_password`` is the only signal about its presence.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    name: str
    description: str
    conn_secret_ref: str | None
    host: str | None
    port: int | None
    database: str | None
    username: str | None
    has_password: bool
    sslmode: str | None
    allowed_tables: list[str]
    sensitive_columns: list[str]


class DbSourceOut(BaseModel):
    id: int
    application_id: int
    name: str
    description: str
    conn_secret_ref: str | None
    host: str | None
    port: int | None
    database: str | None
    username: str | None
    # Whether a password is configured. We never echo the raw password back.
    has_password: bool
    sslmode: str | None
    allowed_tables: list[str]
    sensitive_columns: list[str]


class ApplicationIntegrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: Literal["redis", "kafka", "clickhouse"]
    config: dict[str, Any]
    secret_ref: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _validate_config(self) -> "ApplicationIntegrationIn":
        self.config = normalize_integration_config(self.kind, self.config)
        return self


class ApplicationIntegrationUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    secret_ref: str | None = Field(default=None, min_length=1, max_length=4000)
    state: str | None = Field(default=None, pattern="^(active|disabled)$")


class ApplicationIntegrationOut(BaseModel):
    id: int
    application_id: int
    name: str
    kind: str
    state: str
    readonly_verified_at: datetime | None
    last_collected_at: datetime | None
    last_error: str | None


class ApplicationIntegrationConfigurationOut(ApplicationIntegrationOut):
    """Admin-only view of non-secret connection selectors."""

    config: dict[str, Any]


class UpdateDbSourceIn(BaseModel):
    """Partial update for an existing data source.

    Every field is optional. ``password`` is only overwritten when a non-empty
    value is supplied, so an operator can rotate metadata without re-pasting the
    secret. Supplying neither a structured connection nor a secret ref leaves
    the existing connection mode untouched (you cannot blank out the only way to
    reach the source).
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    host: str | None = Field(default=None, max_length=500)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=2000)
    conn_secret_ref: str | None = Field(default=None, max_length=1000)
    sslmode: Literal["verify-full"] | None = None

    @field_validator("conn_secret_ref")
    @classmethod
    def _require_environment_secret_reference(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"env://[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("conn_secret_ref must be an env://NAME reference")
        return value
    allowed_tables: list[str] | None = Field(default=None, min_length=1, max_length=100)
    sensitive_columns: list[str] | None = None


class RunApprovedQueryIn(BaseModel):
    """A query-catalog request, never operator-authored SQL."""

    model_config = ConfigDict(extra="forbid")

    source_id: int = Field(gt=0)
    table: str = Field(min_length=1, max_length=300)
    operation: Literal["sample", "count"]


class CreateApplicationDescriptionIn(BaseModel):
    description_type: str = Field(default="deploy", pattern="^(deploy|other)$")
    content: str = Field(min_length=1, max_length=10000)


class ApplicationDescriptionOut(BaseModel):
    id: int
    application_id: int
    description_type: str
    content: str


class AlertListOut(BaseModel):
    id: int
    dedupe_key: str
    application_id: int
    application_name: str
    topic: str
    title: str
    level: str
    error_message: str
    received_at: datetime


class DeadLetterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    topic: str
    dedupe_key: str | None = None
    payload: dict | None = None
    reason: str | None = None
    replayed: bool
    created_at: datetime


class ReplayOut(BaseModel):
    id: int
    topic: str
    status: str


# --- Audit log (admin read) -------------------------------------------------
#
# The append-only `audit_events` table is written by every privileged
# control-plane mutation (see `audit_action`). These schemas make that trail
# *observable*: `AuditEventOut` is the per-row projection and `AuditEventListOut`
# carries pagination metadata (total / limit / offset) so the UI can page
# without re-counting on the client.

class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    application_id: int | None
    request_id: str | None
    trace_id: str | None
    result: str
    detail: dict | None
    created_at: datetime


class AuditEventListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditEventOut]


class AuthLoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    token: str
    user: UserOut


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    status: str
    created_at: datetime


# --- AI model configuration (admin) -------------------------------------

# --- Git credentials & repository registry (admin) -----------------------
#
# Global, read-only Git accounts (``git_credentials``) and the repository
# registry (``git_repos``) that applications bind to. Both are admin-only
# write surfaces. The ``secret_ref`` is encrypted at rest via ``_store_key_ref``
# (same pattern as AI-model keys); on update it is optional so an operator can
# rotate metadata without re-pasting the secret.

class GitCredentialIn(BaseModel):
    auth_type: str = Field(pattern="^(ssh|https)$")
    username: str = Field(default="", max_length=200)
    # Supports ``env://NAME`` (preferred) or a literal secret. Required on
    # create; optional on update.
    secret_ref: str = Field(min_length=1, max_length=2000)
    readonly: bool = True
    note: str = Field(default="", max_length=2000)


class GitCredentialUpdateIn(BaseModel):
    """Partial update for an existing Git credential.

    Every field is optional. ``secret_ref`` is only overwritten when a
    non-empty value is supplied, so metadata can be rotated without re-pasting
    the secret. Supplying nothing leaves the existing credential untouched.
    """

    auth_type: str | None = Field(default=None, pattern="^(ssh|https)$")
    username: str | None = Field(default=None, max_length=200)
    secret_ref: str | None = Field(default=None, max_length=2000)
    readonly: bool | None = None
    note: str | None = Field(default=None, max_length=2000)


class GitCredentialOut(BaseModel):
    id: int
    auth_type: str
    username: str
    readonly: bool
    note: str
    has_secret: bool


class GitRepoIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Unique across the registry (DB constraint); the route returns 409 on a
    # duplicate.
    repo_url: str = Field(min_length=1, max_length=2000)
    default_branch: str = Field(default="main", max_length=200)
    # Provider family: github / gitlab / gitee / bitbucket / other. Free-text
    # so new hosts can be onboarded without a schema change.
    repo_type: str = Field(default="other", max_length=32)
    # Optional link to a global credential. ``None`` leaves the repo without a
    # default account (repos fall back to the global read-only account).
    credential_id: int | None = Field(default=None, gt=0)


class GitRepoUpdateIn(BaseModel):
    """Partial update for an existing repository.

    Every field is optional; ``repo_url`` uniqueness is still enforced on
    update (a 409 is returned if the new value collides with another repo).
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    repo_url: str | None = Field(default=None, min_length=1, max_length=2000)
    default_branch: str | None = Field(default=None, max_length=200)
    repo_type: str | None = Field(default=None, max_length=32)
    credential_id: int | None = Field(default=None, gt=0)


class GitRepoOut(BaseModel):
    id: int
    name: str
    repo_url: str
    default_branch: str
    scope: str
    application_id: int | None
    repo_type: str
    credential_id: int | None


class AiModelConfigIn(BaseModel):
    provider: str = Field(pattern="^(openai|anthropic)$")
    base_url: str = Field(min_length=1, max_length=1000)
    # Supports `env://NAME` (preferred, secret stays in env) or a literal key.
    # Optional on update: when omitted/empty the existing reference is kept, so
    # operators can edit metadata without re-pasting the secret.
    api_key_ref: str | None = Field(default=None, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    is_default: bool = False


class AiModelConfigOut(BaseModel):
    id: int
    provider: str
    base_url: str
    model: str
    is_default: bool
    has_key: bool


class AiOutputLanguageIn(BaseModel):
    """The language used for every human-readable AI analysis result."""

    language: Literal["en", "zh"]


class AiOutputLanguageOut(BaseModel):
    language: Literal["en", "zh"]


class EvidenceConnectorIn(BaseModel):
    """Administrator-owned, capability-limited evidence connector."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: Literal["loki", "prometheus", "tempo", "postgres", "redis", "kafka", "clickhouse"]
    config: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = Field(default=None, max_length=4000)
    diagnostic_profile: dict[str, Any] = Field(default_factory=dict)
    collection_budget_seconds: int = Field(default=15, ge=1, le=60)
    state: Literal["active", "disabled"] = "active"


class EvidenceConnectorUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    secret_ref: str | None = Field(default=None, max_length=4000)
    diagnostic_profile: dict[str, Any] | None = None
    collection_budget_seconds: int | None = Field(default=None, ge=1, le=60)
    state: Literal["active", "disabled"] | None = None


class EvidenceConnectorOut(BaseModel):
    id: int
    application_id: int
    name: str
    kind: str
    state: str
    config: dict[str, Any]
    diagnostic_profile: dict[str, Any]
    collection_budget_seconds: int
    has_secret: bool


# --- User management (admin) --------------------------------------------

class UserCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    role: str = Field(default="user", pattern="^(admin|user)$")
    password: str = Field(min_length=8, max_length=200)


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    status: str | None = Field(default=None, pattern="^(pending|active|disabled)$")


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


# --- Invitations --------------------------------------------------------

class InviteCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class InviteOut(BaseModel):
    id: int
    email: str
    token: str
    status: str
    created_at: datetime


class InviteAcceptIn(BaseModel):
    token: str = Field(min_length=1, max_length=500)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)


class InvestigationErrorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Error", min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=20_000)
    stack: str | None = Field(default=None, max_length=50_000)
    cause: Any = None
    properties: dict[str, Any] = Field(default_factory=dict)


class InvestigationAttachmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["log", "trace", "dependency", "gateway_response"]
    label: str = Field(min_length=1, max_length=1_000)
    content: str = Field(min_length=1, max_length=20_000)


class InvestigationCreateIn(BaseModel):
    """Manual input using the same normalized contract as Kafka intake."""

    model_config = ConfigDict(extra="forbid")

    application_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=500)
    severity: Literal["CRITICAL", "WARNING"] = "WARNING"
    occurred_at: datetime
    error: InvestigationErrorIn
    service_name: str | None = Field(default=None, max_length=300)
    environment: str | None = Field(default=None, max_length=300)
    trace_id: str | None = Field(default=None, max_length=1_000)
    deployment_sha: str | None = Field(default=None, max_length=300)
    fields: dict[str, Any] = Field(default_factory=dict)
    attachments: list[InvestigationAttachmentIn] = Field(default_factory=list, max_length=10)

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value


# --- Application membership (admin / app-admin) -------------------------
#
# These power the per-application Members tab. A membership is a row in
# ``user_application_perms`` (user_id, application_id, perm). The endpoints
# are guarded by ``require_app_perm`` scope "admin" — global admins and
# application admins may read and mutate membership; lower tiers cannot.

class AppMemberOut(BaseModel):
    user_id: int
    email: str
    name: str
    role: str
    status: str
    perm: str


class AppMemberIn(BaseModel):
    user_id: int = Field(gt=0)
    perm: str = Field(pattern="^(read|analyze|admin)$")


class AppMemberUpdateIn(BaseModel):
    perm: str = Field(pattern="^(read|analyze|admin)$")
