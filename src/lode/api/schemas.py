"""Authentication, user, and invitation schemas outside the business API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    status: str
    created_at: datetime


class AuthLoginIn(_StrictInput):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    token: str
    user: UserOut


class UserCreateIn(_StrictInput):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    role: Literal["admin", "user"] = "user"
    password: str = Field(min_length=8, max_length=200)


class UserUpdateIn(_StrictInput):
    name: str | None = Field(default=None, max_length=200)
    role: Literal["admin", "user"] | None = None
    status: Literal["pending", "active", "disabled"] | None = None


class PasswordResetIn(_StrictInput):
    password: str = Field(min_length=8, max_length=200)


class PasswordChangeIn(_StrictInput):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class InviteCreateIn(_StrictInput):
    email: str = Field(min_length=3, max_length=320)


class InviteOut(BaseModel):
    id: int
    email: str
    token: str
    status: str
    created_at: datetime


class InviteAcceptIn(_StrictInput):
    token: str = Field(min_length=1, max_length=500)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)
