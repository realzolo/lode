"""Strict untrusted input contract for one native evidence read candidate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SENTINEL_PATTERN = r"^__LODE_VALUE_REF_[A-Z0-9_]+__$"
LANGUAGES = (
    "logql",
    "elasticsearch_query_dsl",
    "opensearch_query_dsl",
    "sql",
    "https",
    "command",
)
MAX_CANDIDATE_BYTES = 256 * 1024
MAX_STRUCTURE_DEPTH = 64
MAX_STRUCTURE_NODES = 20_000


class DuplicateJSONKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKey(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_structure(value: Any) -> None:
    nodes = 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_STRUCTURE_NODES:
            raise ValueError("candidate structure node limit exceeded")
        if depth > MAX_STRUCTURE_DEPTH:
            raise ValueError("candidate structure depth limit exceeded")
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("candidate contains invalid Unicode") from exc
        elif isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


class RequestedWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @field_validator("start", "end", mode="before")
    @classmethod
    def timestamp_is_string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("window timestamps must be RFC 3339 strings")
        return value

    @field_validator("start", "end")
    @classmethod
    def timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("window timestamps must include timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def range_is_positive(self) -> RequestedWindow:
        if self.end <= self.start:
            raise ValueError("requested window end must follow start")
        return self


class QueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=100_000)


class SearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=2_000)
    body: dict[str, Any]


class HTTPSPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "HEAD"]
    url: str = Field(min_length=1, max_length=4_000)
    query: dict[str, Any]
    body: dict[str, Any] | None


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str = Field(min_length=1, max_length=500)
    argv: list[str] = Field(max_length=100)
    working_set_id: str = Field(min_length=1, max_length=200)

    @field_validator("argv")
    @classmethod
    def argv_items_are_bounded(cls, value: list[str]) -> list[str]:
        if any(len(item) > 4_000 for item in value):
            raise ValueError("argv item exceeds 4000 characters")
        return value


class NativeReadCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["native-read-candidate.v1"]
    action_id: str = Field(min_length=1, max_length=200)
    connector_id: int = Field(gt=0)
    language: Literal[
        "logql",
        "elasticsearch_query_dsl",
        "opensearch_query_dsl",
        "sql",
        "https",
        "command",
    ]
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_evidence: str = Field(min_length=1, max_length=2_000)
    evidence_anchors: list[str] = Field(min_length=1, max_length=20)
    payload: QueryPayload | SearchPayload | HTTPSPayload | CommandPayload
    value_bindings: dict[str, str] = Field(max_length=20)
    requested_window: RequestedWindow | None
    requested_limit: int = Field(gt=0)
    requested_timeout_ms: int = Field(gt=0)

    @field_validator("evidence_anchors")
    @classmethod
    def anchors_are_unique_and_bounded(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence anchors must be unique")
        if any(not item or len(item) > 200 for item in value):
            raise ValueError("evidence anchor is invalid")
        return value

    @field_validator("value_bindings")
    @classmethod
    def bindings_are_canonical(cls, value: dict[str, str]) -> dict[str, str]:
        import re

        pattern = re.compile(SENTINEL_PATTERN)
        if any(not pattern.fullmatch(key) for key in value):
            raise ValueError("value binding sentinel is invalid")
        if any(not ref or len(ref) > 200 for ref in value.values()):
            raise ValueError("ValueRef is invalid")
        if len(set(value.values())) != len(value.values()):
            raise ValueError("each ValueRef must use one unique sentinel")
        return value

    @model_validator(mode="after")
    def payload_matches_language(self) -> NativeReadCandidateInput:
        expected = {
            "logql": QueryPayload,
            "sql": QueryPayload,
            "elasticsearch_query_dsl": SearchPayload,
            "opensearch_query_dsl": SearchPayload,
            "https": HTTPSPayload,
            "command": CommandPayload,
        }[self.language]
        if not isinstance(self.payload, expected):
            raise ValueError(f"payload does not match language {self.language}")
        _validate_structure(self.model_dump(mode="json"))
        return self


def parse_candidate_json(raw: bytes | str) -> NativeReadCandidateInput:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_CANDIDATE_BYTES:
        raise ValueError("candidate exceeds byte limit")
    try:
        text = encoded.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJSONKey) as exc:
        raise ValueError(f"invalid candidate JSON: {exc}") from exc
    _validate_structure(value)
    return NativeReadCandidateInput.model_validate(value)
