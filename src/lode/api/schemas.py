"""API request/response schemas.

These are intentionally decoupled from the ORM models: they describe exactly
what the frontend needs (joined titles, latest levels, workflow steps) and
keep internal columns (ids, secrets, raw payloads in full) out of the wire.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalysisStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_type: str
    status: str
    order_index: int
    detail: str | None = None
    summary: str | None = None


class AnalysisHintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author: str
    content: str
    created_at: datetime


class AlertSummary(BaseModel):
    title: str
    level: str
    env: str
    topic: str
    error_message: str
    fields: dict


class AnalysisListOut(BaseModel):
    dedupe_key: str
    application_id: int
    application_name: str
    title: str
    level: str
    status: str
    confidence: float | None
    conclusion: str | None
    received_at: datetime | None
    updated_at: datetime
    # The caller's permission on this analysis's application, or ``None`` when
    # the caller is a global admin (unrestricted). Surfaced so the UI can gate
    # actions like re-analyze.
    my_perm: str | None = None


class AnalysisDetailOut(BaseModel):
    dedupe_key: str
    application_id: int
    application_name: str
    status: str
    confidence: float | None
    conclusion: str | None
    evidence: dict | None
    alert: AlertSummary | None
    steps: list[AnalysisStepOut]
    hints: list[AnalysisHintOut]
    matched_memory: str | None = None
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    # The caller's permission on this application, or ``None`` for a global
    # admin (unrestricted).
    my_perm: str | None = None


class ApplicationOut(BaseModel):
    id: int
    name: str
    topic: str | None
    latest_level: str
    repo_count: int
    created_at: datetime


class ApplicationDetailOut(BaseModel):
    id: int
    name: str
    topic: str | None
    created_at: datetime
    repos: list[dict]
    preset_prompts: list[dict]
    db_sources: list[DbSourceListItem]


class CreateApplicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# --- Application configuration (admin only) ----------------------------
#
# These power the per-application edit forms: Kafka topic binding, repository
# binding, read-only data sources, preset prompts. The on-the-wire shapes are
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


class BindRepoIn(BaseModel):
    repo_id: int = Field(gt=0)
    description: str = Field(default="", max_length=2000)


class ApplicationRepoOut(BaseModel):
    id: int
    application_id: int
    repo_id: int
    repo_name: str
    repo_url: str
    description: str


class CreateDbSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
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
    # TLS mode for structured connections (NULL/omitted = libpq default
    # "prefer"). Use "require"/"verify-full" for cross-network links.
    sslmode: str | None = Field(default=None, max_length=32)
    allowed_tables: list[str] = Field(default_factory=list)
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
        if self.host and not self.database:
            raise ValueError("database is required when host is set")
        if self.database and not self.host:
            raise ValueError("host is required when database is set")
        return self


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


class UpdateDbSourceIn(BaseModel):
    """Partial update for an existing data source.

    Every field is optional. ``password`` is only overwritten when a non-empty
    value is supplied, so an operator can rotate metadata without re-pasting the
    secret. Supplying neither a structured connection nor a secret ref leaves
    the existing connection mode untouched (you cannot blank out the only way to
    reach the source).
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    host: str | None = Field(default=None, max_length=500)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=2000)
    conn_secret_ref: str | None = Field(default=None, max_length=1000)
    sslmode: str | None = Field(default=None, max_length=32)
    allowed_tables: list[str] | None = None
    sensitive_columns: list[str] | None = None


class RunQueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    source_id: int | None = None
    desensitize: bool = True


class CreatePresetPromptIn(BaseModel):
    type: str = Field(default="deploy", pattern="^(deploy|other)$")
    content: str = Field(min_length=1, max_length=10000)


class PresetPromptOut(BaseModel):
    id: int
    application_id: int
    type: str
    content: str


class MemoryOut(BaseModel):
    id: int
    application_id: int
    application_name: str
    trigger_signature: str
    content: str
    is_valid: bool
    created_at: datetime


class AlertListOut(BaseModel):
    id: int
    dedupe_key: str
    application_id: int
    application_name: str
    topic: str
    title: str
    level: str
    env: str
    error_message: str
    received_at: datetime


class AddHintIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    author: str = Field(default="", max_length=120)


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
    repo_type: str
    credential_id: int | None


class AiModelConfigIn(BaseModel):
    scope: str = Field(pattern="^(global|application)$")
    application_id: int | None = None
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
    scope: str
    application_id: int | None
    provider: str
    base_url: str
    model: str
    is_default: bool
    has_key: bool


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


class ReanalyzeOut(BaseModel):
    dedupe_key: str
    status: str
    message: str


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
