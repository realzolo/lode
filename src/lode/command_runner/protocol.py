"""Strict signed wire protocol shared by the worker and isolated runner."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from time import time
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RunnerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["rg_fixed_search"]
    executable: Literal["/usr/bin/rg"]
    binary_sha256: str
    argv: list[str] = Field(min_length=9, max_length=9)
    pattern_index: Literal[7]
    working_set_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,127}$")
    working_root: str = Field(pattern=r"^/worksets/[a-z0-9][a-z0-9_-]{0,127}$")
    allowed_files: list[str] = Field(min_length=1, max_length=1)
    timeout_ms: int = Field(ge=1, le=15_000)
    output_bytes: int = Field(ge=1, le=2 * 1024 * 1024)
    result_limit: int = Field(ge=1, le=1_000)

    @field_validator("binary_sha256")
    @classmethod
    def binary_hash_is_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("runner binary hash is invalid")
        return value


class RunnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    authorized_read_id: int = Field(gt=0)
    issued_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)
    action: RunnerAction


class SignedRunnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: RunnerRequest
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunnerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["succeeded", "failed"]
    exit_code: int | None
    stdout: str
    stderr: str
    output_bytes: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    failure_code: (
        Literal["sandbox_violation", "provider_timeout", "cost_exceeded", "invalid_response"] | None
    )
    secret_categories: list[str] = Field(max_length=20)
    prompt_injection_detected: bool
    output_sha256: str | None

    @field_validator("output_sha256")
    @classmethod
    def output_hash_is_sha256(cls, value: str | None) -> str | None:
        if value is not None and _SHA256.fullmatch(value) is None:
            raise ValueError("runner output hash is invalid")
        return value

    @model_validator(mode="after")
    def status_fields_are_consistent(self) -> Self:
        if self.status == "succeeded" and (
            self.failure_code is not None or self.output_sha256 is not None
        ):
            raise ValueError("successful runner result has failure metadata")
        if self.status == "failed" and (self.failure_code is None or self.stdout or self.stderr):
            raise ValueError("failed runner result exposes output or lacks a failure code")
        return self


def canonical_request(request: RunnerRequest) -> bytes:
    return json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def sign_request(request: RunnerRequest, key: str) -> str:
    if len(key.encode()) < 32:
        raise ValueError("command runner key must contain at least 32 bytes")
    return hmac.new(key.encode(), canonical_request(request), hashlib.sha256).hexdigest()


def verify_request(envelope: SignedRunnerRequest, key: str, *, now: int | None = None) -> None:
    current = int(time()) if now is None else now
    request = envelope.request
    expected = sign_request(request, key)
    if not hmac.compare_digest(expected, envelope.signature):
        raise PermissionError("runner request signature is invalid")
    if (
        request.issued_at > current + 5
        or request.expires_at < current
        or request.expires_at > current + 60
    ):
        raise PermissionError("runner request is outside its validity window")
