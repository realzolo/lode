"""API request/response schemas.

These are intentionally decoupled from the ORM models: they describe exactly
what the frontend needs (joined titles, latest levels, workflow steps) and
keep internal columns (ids, secrets, raw payloads in full) out of the wire.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lode.integration_policy import (
    integration_kind,
    normalize_integration_config,
    normalize_integration_secrets,
)


class ApplicationOut(BaseModel):
    id: int
    name: str
    ingestion_topic: str
    latest_level: str
    model_configured: bool
    model_available: bool
    ingestion_state: Literal["draft", "active", "paused"]
    ingestion_observed_state: Literal["draft", "starting", "listening", "paused", "error"]
    ingestion_start_position: Literal["earliest", "latest"] | None = None
    my_perm: str | None = None
    created_at: datetime


class ApplicationDetailOut(BaseModel):
    id: int
    name: str
    ingestion_topic: str
    model_config_id: int | None
    ingestion_state: Literal["draft", "active", "paused"]
    created_at: datetime
    repos: list[dict]
    architecture_contexts: list[dict]
    integrations: list["ApplicationIntegrationOut"] = Field(default_factory=list)
    my_perm: str | None = None


class CreateApplicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ingestion_topic: str = Field(min_length=1, max_length=500)

    @field_validator("name", "ingestion_topic")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


# --- Application configuration (admin only) ----------------------------
#
# These power the per-application edit forms: Kafka ingestion, repository
# binding, integrations, model selection, and architecture context. The shapes are
# intentionally minimal — only what the Settings tabs need to read back into
# their forms. Application-owned writes use ``require_app_perm`` with the
# ``admin`` scope; global admins satisfy that scope automatically.

class SetApplicationTopicIn(BaseModel):
    ingestion_topic: str = Field(min_length=1, max_length=500)

    @field_validator("ingestion_topic")
    @classmethod
    def strip_topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ingestion_topic must not be blank")
        return value


class ApplicationTopicOut(BaseModel):
    application_id: int
    ingestion_topic: str


class StartApplicationIngestionIn(BaseModel):
    start_position: Literal["earliest", "latest"]


class ApplicationIngestionStatusOut(BaseModel):
    application_id: int
    ingestion_topic: str
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


class ModelAvailabilityOut(BaseModel):
    available: bool
    endpoint: str
    latency_ms: int
    error_code: str | None = None
    error_detail: str | None = None


class ApplicationModelOut(BaseModel):
    application_id: int
    model_config_id: int | None
    model_test: ModelAvailabilityOut | None = None


class ApplicationIntegrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    config: dict[str, Any]
    secrets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_config(self) -> "ApplicationIntegrationIn":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name must not be blank")
        self.config = normalize_integration_config(self.kind, self.config)
        self.secrets = normalize_integration_secrets(self.kind, self.secrets)
        return self


class ApplicationIntegrationUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None
    state: str | None = Field(default=None, pattern="^(active|disabled)$")

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class ApplicationIntegrationOut(BaseModel):
    id: int
    application_id: int
    name: str
    kind: str
    kind_version: int
    revision: int
    state: str
    verification_status: str
    verified_at: datetime | None
    last_collected_at: datetime | None
    last_error: str | None
    configured_secret_fields: list[str]


class ApplicationIntegrationConfigurationOut(ApplicationIntegrationOut):
    """Admin-only view of non-secret connection selectors."""

    config: dict[str, Any]


class IntegrationKindOut(BaseModel):
    kind: str
    version: int
    label: str
    capabilities: list[str]
    form: list[dict[str, Any]]


class RunApprovedQueryIn(BaseModel):
    """A query-catalog request, never operator-authored SQL."""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1, max_length=300)
    operation: Literal["sample", "count"]


class CreateApplicationArchitectureContextIn(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class ApplicationArchitectureContextOut(BaseModel):
    id: int
    application_id: int
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
# write surfaces. Secrets are encrypted at rest; on update they are optional so an operator can
# rotate metadata without re-pasting the secret.

class GitCredentialIn(BaseModel):
    auth_type: str = Field(pattern="^(ssh|https)$")
    username: str = Field(default="", max_length=200)
    secret: str = Field(min_length=1, max_length=2000)
    readonly: bool = True
    note: str = Field(default="", max_length=2000)


class GitCredentialUpdateIn(BaseModel):
    """Partial update for an existing Git credential.

    Every field is optional. ``secret`` is only overwritten when a
    non-empty value is supplied, so metadata can be rotated without re-pasting
    the secret. Supplying nothing leaves the existing credential untouched.
    """

    auth_type: str | None = Field(default=None, pattern="^(ssh|https)$")
    username: str | None = Field(default=None, max_length=200)
    secret: str | None = Field(default=None, max_length=2000)
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
    # Optional on update: when omitted/empty the existing encrypted key is kept, so
    # operators can edit metadata without re-pasting the secret.
    api_key: str | None = Field(default=None, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    is_default: bool = False


class AiModelConfigOut(BaseModel):
    id: int
    provider: str
    base_url: str
    model: str
    is_default: bool
    has_key: bool
    last_test_status: Literal["untested", "available", "unavailable"]
    last_tested_at: datetime | None
    last_test_latency_ms: int | None
    last_test_error_code: str | None
    last_test_error_detail: str | None


class AiOutputLanguageIn(BaseModel):
    """The language used for every human-readable AI analysis result."""

    language: Literal["en", "zh"]


class AiOutputLanguageOut(BaseModel):
    language: Literal["en", "zh"]


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
