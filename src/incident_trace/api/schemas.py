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
