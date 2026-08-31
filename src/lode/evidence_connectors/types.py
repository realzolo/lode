"""Provider-neutral connector, transport, and normalized evidence contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from lode.evidence_access.types import EvidenceExecutionFailure


class ProviderExecutionError(EvidenceExecutionFailure):
    def __init__(self, code: str, reason: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(code, reason, detail)


class DuplicateProviderKey(ValueError):
    pass


class EvidenceResultEnvelope(dict[str, Any]):
    """Expose only a safe projection while carrying raw data to the archiver."""

    __slots__ = ("sealed_raw",)

    def __init__(self, projected: Mapping[str, Any], sealed_raw: Any) -> None:
        super().__init__(projected)
        self.sealed_raw = sealed_raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise DuplicateProviderKey(key)
        output[key] = value
    return output


def decode_provider_json(body: bytes, *, max_nodes: int = 100_000, max_depth: int = 64) -> Any:
    try:
        value = json.loads(body.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateProviderKey) as exc:
        raise ProviderExecutionError("invalid_response", "provider returned invalid JSON") from exc
    nodes = 0
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise ProviderExecutionError(
                "invalid_response", "provider response structure is too large"
            )
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ProviderExecutionError(
                    "invalid_response", "provider returned invalid Unicode"
                ) from exc
    return value


@dataclass(frozen=True, slots=True)
class ProviderHTTPResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class ProviderHTTPTransport(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout_ms: int,
    ) -> ProviderHTTPResponse: ...


@dataclass(frozen=True, slots=True)
class VerificationResult:
    provider: str
    version: str
    credential_identity_hash: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeSchemaCatalog:
    provider: str
    version: str
    resources: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class IntrospectionBudget:
    timeout_ms: int
    max_resources: int
    window_start: datetime | None = None
    window_end: datetime | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_ms, bool)
            or not 1 <= self.timeout_ms <= 10_000
            or isinstance(self.max_resources, bool)
            or not 1 <= self.max_resources <= 10_000
        ):
            raise ValueError("connector introspection budget is invalid")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("connector introspection window must have both bounds")
        if self.window_start is not None and (
            self.window_start.tzinfo is None
            or self.window_end is None
            or self.window_end.tzinfo is None
            or self.window_start >= self.window_end
        ):
            raise ValueError("connector introspection window is invalid")


class EvidenceConnectorContract(Protocol):
    async def verify(self) -> VerificationResult: ...
    async def introspect(
        self, scope: Mapping[str, Any], budget: IntrospectionBudget
    ) -> NativeSchemaCatalog: ...
