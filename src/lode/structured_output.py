"""Closed, provider-safe contracts for model-generated structured output."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

MAX_JSON_DOCUMENT_BYTES = 256 * 1024
MAX_JSON_DOCUMENT_DEPTH = 64
MAX_JSON_DOCUMENT_NODES = 20_000


class StrictResponseSchemaError(ValueError):
    """Raised when a response schema is outside the supported strict subset."""


class StrictResponseModel(BaseModel):
    """Base class for model output DTOs accepted by every registered protocol."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def response_json_schema(cls) -> dict[str, Any]:
        schema = cls.model_json_schema()
        validate_strict_response_schema(schema)
        return schema


class ProtocolHealthDetail(StrictResponseModel):
    protocol: Literal["structured_output"]
    checks: tuple[Literal["required", "nullable", "nested"], ...]


class ProtocolHealthPayload(StrictResponseModel):
    ok: Literal[True]
    detail: ProtocolHealthDetail
    nullable: str | None

    @model_validator(mode="after")
    def probe_values_are_exact(self):
        if self.detail.checks != ("required", "nullable", "nested"):
            raise ValueError("protocol health checks are incomplete")
        if self.nullable is not None:
            raise ValueError("protocol health nullable field is not null")
        return self


def protocol_health_json_schema() -> dict[str, Any]:
    return ProtocolHealthPayload.response_json_schema()


def validate_strict_response_schema(schema: dict[str, Any]) -> None:
    """Fail closed on schemas that a strict structured-output provider cannot enforce."""

    def walk(node: Any, path: str) -> None:
        if isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
            return
        if not isinstance(node, dict):
            return
        if not node:
            raise StrictResponseSchemaError(f"{path} is unconstrained")
        if "default" in node:
            raise StrictResponseSchemaError(f"{path} defines a default")
        if "additionalProperties" in node and node["additionalProperties"] is not False:
            raise StrictResponseSchemaError(f"{path} is an open object")
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties")
            if not isinstance(properties, dict) or not properties:
                raise StrictResponseSchemaError(f"{path} has no closed properties")
            required = node.get("required")
            if not isinstance(required, list) or set(required) != set(properties):
                raise StrictResponseSchemaError(
                    f"{path} must require every declared property"
                )
            if node.get("additionalProperties") is not False:
                raise StrictResponseSchemaError(f"{path} must forbid additional properties")
        for key, child in node.items():
            walk(child, f"{path}.{key}")

    walk(schema, "$")


def parse_json_document(value: str) -> Any:
    """Decode one bounded JSON document while rejecting duplicate object keys."""
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_JSON_DOCUMENT_BYTES:
        raise ValueError("JSON document exceeds byte limit")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, child in pairs:
            if key in result:
                raise ValueError("JSON document contains a duplicate key")
            result[key] = child
        return result

    def invalid_constant(_value: str) -> None:
        raise ValueError("JSON document contains a non-finite number")

    try:
        decoded = json.loads(
            value,
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("JSON document is invalid") from exc
    nodes = 0
    stack = [(decoded, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_DOCUMENT_NODES:
            raise ValueError("JSON document exceeds node limit")
        if depth > MAX_JSON_DOCUMENT_DEPTH:
            raise ValueError("JSON document exceeds depth limit")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("JSON document contains invalid Unicode") from exc
    return decoded
