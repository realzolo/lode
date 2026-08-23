"""API request/response schemas.

These are intentionally decoupled from the ORM models: they describe exactly
what the frontend needs (joined titles, latest levels, workflow steps) and
keep internal columns (ids, secrets, raw payloads in full) out of the wire.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    db_sources: list[dict]


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
    conn_secret_ref: str = Field(min_length=1, max_length=1000)
    """Reference to a secret (e.g. ``env://DATABASE_URL``) — the actual DSN
    lives in the deployment environment, never in the DB row.
    """
    allowed_tables: list[str] = Field(default_factory=list)


class DbSourceOut(BaseModel):
    id: int
    application_id: int
    name: str
    conn_secret_ref: str
    allowed_tables: list[str]


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
