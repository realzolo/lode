"""Authentication, user, and invitation schemas outside the business API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    status: str
    is_system_admin: bool
    must_change_password: bool
    created_at: datetime


class AuthLoginIn(_StrictInput):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    token: str
    user: UserOut


class UserCreateIn(_StrictInput):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(default="", max_length=200)
    initial_password: str = Field(min_length=8, max_length=200)


class UserUpdateIn(_StrictInput):
    display_name: str | None = Field(default=None, max_length=200)
    status: Literal["active", "disabled"] | None = None


class PasswordResetIn(_StrictInput):
    password: str = Field(min_length=8, max_length=200)


class PasswordChangeIn(_StrictInput):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class WorkspaceMemberPutIn(_StrictInput):
    permission: Literal["viewer", "operator"]


class WorkspaceMemberOut(BaseModel):
    user_id: int
    username: str
    display_name: str
    status: Literal["active", "disabled"]
    permission: Literal["viewer", "operator"]
