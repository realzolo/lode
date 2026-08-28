"""Grafana Loki query-only connector and normalization."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_access.loki_scope import selector_for_branch
from lode.evidence_connectors.common import (
    classify_response,
    credential_identity_hash,
    response_json,
)
from lode.evidence_connectors.safety import sanitize_evidence
from lode.evidence_connectors.transport import (
    BoundedHTTPTransport,
    validate_base_url,
)
from lode.evidence_connectors.types import (
    IntrospectionBudget,
    NativeSchemaCatalog,
    ProviderExecutionError,
    ProviderHTTPTransport,
    VerificationResult,
)

_VERSION = re.compile(r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:\.[0-9]+)?")
_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class LokiConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(max_length=1_000)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=200)
    max_response_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=16 * 1024 * 1024)

    @field_validator("base_url")
    @classmethod
    def base_url_is_origin(cls, value: str) -> str:
        return validate_base_url(value)[0]

class LokiConnector:
    kind = "loki"
    language = "logql"
    read_capabilities = ("bounded_log_query", "bounded_metric_query", "schema_introspection")

    def __init__(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        transport: ProviderHTTPTransport | None = None,
    ) -> None:
        self.config = LokiConnectorConfig.model_validate(config)
        if set(secrets) not in (set(), {"bearer_token"}) or any(
            not value for value in secrets.values()
        ):
            raise ValueError("Loki secrets may contain only a non-empty bearer_token")
        self.secrets = dict(secrets)
        headers = {"accept": "application/json"}
        if "bearer_token" in secrets:
            headers["authorization"] = f"Bearer {secrets['bearer_token']}"
        if self.config.tenant_id is not None:
            headers["x-scope-orgid"] = self.config.tenant_id
        self.transport = transport or BoundedHTTPTransport(
            base_url=self.config.base_url,
            headers=headers,
            max_response_bytes=self.config.max_response_bytes,
        )
        self._version: str | None = None

    async def verify(self) -> VerificationResult:
        ready = await self.transport.request("GET", "/ready", timeout_ms=5_000)
        classify_response(ready)
        if ready.body.strip().lower() != b"ready":
            raise ProviderExecutionError("invalid_response", "Loki readiness response is invalid")
        build = await self.transport.request(
            "GET", "/loki/api/v1/status/buildinfo", timeout_ms=5_000
        )
        payload = response_json(build)
        version = payload.get("version") if isinstance(payload, dict) else None
        match = _VERSION.match(version) if isinstance(version, str) else None
        if match is None or int(match.group("major")) != 3:
            observed_version = match.group(0) if match is not None else "unknown"
            raise ProviderExecutionError(
                "unsupported_version",
                f"Unsupported Loki version {observed_version}. This connector requires Loki 3.x.",
                {
                    "provider": "loki",
                    "observed_version": observed_version,
                    "supported_major_versions": [3],
                },
            )
        self._version = version
        identity = credential_identity_hash(self.secrets or {"anonymous": self.config.base_url})
        return VerificationResult(self.kind, version, identity, self.read_capabilities)

    async def introspect(
        self, scope: Mapping[str, Any], budget: IntrospectionBudget
    ) -> NativeSchemaCatalog:
        branches = scope.get("root_filter_dnf")
        if (
            not isinstance(branches, list)
            or not branches
            or len(branches) > 8
            or any(not isinstance(branch, list) or not branch for branch in branches)
        ):
            raise ProviderExecutionError(
                "invalid_response", "Loki introspection requires a normalized root filter"
            )
        if budget.window_start is None or budget.window_end is None:
            raise ProviderExecutionError(
                "cost_exceeded", "Loki introspection requires an absolute time window"
            )
        if (budget.window_end - budget.window_start).total_seconds() > 3_600:
            raise ProviderExecutionError(
                "cost_exceeded", "Loki introspection window exceeds one hour"
            )
        labels: set[str] = set()
        series_count = 0
        for branch in branches:
            try:
                selector = selector_for_branch(branch)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderExecutionError("invalid_response", "Loki root filter is invalid") from exc
            response = await self.transport.request(
                "GET",
                "/loki/api/v1/series",
                query={
                    "match[]": selector,
                    "start": budget.window_start.isoformat(),
                    "end": budget.window_end.isoformat(),
                },
                timeout_ms=budget.timeout_ms,
            )
            payload = response_json(response)
            if (
                not isinstance(payload, dict)
                or payload.get("status") != "success"
                or not isinstance(payload.get("data"), list)
            ):
                raise ProviderExecutionError("invalid_response", "Loki series response is invalid")
            series_count += len(payload["data"])
            if series_count > budget.max_resources:
                raise ProviderExecutionError("cost_exceeded", "Loki series catalog is too large")
            for series in payload["data"]:
                if not isinstance(series, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in series.items()
                ):
                    raise ProviderExecutionError("invalid_response", "Loki series catalog is invalid")
                labels.update(series)
                if len(labels) > budget.max_resources:
                    raise ProviderExecutionError("cost_exceeded", "Loki label catalog is too large")
        return NativeSchemaCatalog(
            provider=self.kind,
            version=self._version or "unverified",
            resources={"labels": sorted(labels), "root_filter_dnf": branches},
        )

    async def preflight(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        ready = await self.transport.request(
            "GET", "/ready", timeout_ms=min(5_000, action["timeout_ms"])
        )
        classify_response(ready)
        if ready.body.strip().lower() != b"ready":
            raise ProviderExecutionError("invalid_response", "Loki readiness response is invalid")
        return {"provider": self.kind, "status": "ready", "version": self._version}

    async def execute(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        normalized_branches: list[dict[str, Any]] = []
        branch_timeout = max(1, action["timeout_ms"] // len(action["queries"]))
        for branch_query in action["queries"]:
            query = {
                "query": branch_query,
                "start": action["start"],
                "end": action["end"],
                "limit": str(action["limit"]),
                "direction": action["direction"],
            }
            if action.get("step_seconds") is not None:
                query["step"] = str(action["step_seconds"])
            response = await self.transport.request(
                "GET",
                "/loki/api/v1/query_range",
                query=query,
                timeout_ms=branch_timeout,
            )
            normalized_branches.append(self._normalize(response_json(response), action["limit"]))
        result_types = {item["result_type"] for item in normalized_branches}
        if len(result_types) != 1:
            raise ProviderExecutionError("invalid_response", "Loki branch result types differ")
        records_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        truncated = False
        for item in normalized_branches:
            truncated = truncated or bool(item["truncated"])
            for record in item["records"]:
                key = (
                    record["timestamp"],
                    json.dumps(record["labels"], sort_keys=True),
                    record["value"],
                )
                records_by_key[key] = record
        records = sorted(
            records_by_key.values(),
            key=lambda item: (
                float(item["timestamp"]),
                json.dumps(item["labels"], sort_keys=True),
                item["value"],
            ),
        )
        normalized = {
            "provider": "loki",
            "result_type": next(iter(result_types)),
            "records": records[: action["limit"]],
            "record_count": min(len(records), action["limit"]),
            "truncated": truncated or len(records) > action["limit"],
            "statistics": {"branch_count": len(normalized_branches)},
        }
        sanitized, categories, injection = sanitize_evidence(normalized)
        return {
            **sanitized,
            "secret_categories": list(categories),
            "prompt_injection_detected": injection,
        }

    @staticmethod
    def _action(permit: ExecutionPermit) -> Mapping[str, Any]:
        if not isinstance(permit, ExecutionPermit):
            raise PermissionError("Loki adapter requires an internal execution permit")
        permit.assert_valid()
        action = permit.action
        required = {
            "adapter_kind",
            "queries",
            "query_kind",
            "start",
            "end",
            "limit",
            "direction",
            "timeout_ms",
        }
        if (
            action.get("adapter_kind") != "loki"
            or not required <= set(action)
            or not isinstance(action.get("queries"), list)
            or not 1 <= len(action["queries"]) <= 8
            or any(not isinstance(query, str) or not query for query in action["queries"])
        ):
            raise PermissionError("execution permit is not authorized for Loki")
        return action

    @staticmethod
    def _normalize(payload: Any, limit: int) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise ProviderExecutionError("invalid_response", "Loki query response is invalid")
        if payload.get("warnings"):
            raise ProviderExecutionError("partial_response", "Loki returned query warnings")
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("resultType") not in {
            "streams",
            "matrix",
            "vector",
        }:
            raise ProviderExecutionError("invalid_response", "Loki result type is invalid")
        result = data.get("result")
        if not isinstance(result, list):
            raise ProviderExecutionError("invalid_response", "Loki result must be a list")
        records: list[dict[str, Any]] = []
        result_type = data["resultType"]
        for series in result:
            if not isinstance(series, dict):
                raise ProviderExecutionError("invalid_response", "Loki series is invalid")
            labels = series.get("stream" if result_type == "streams" else "metric", {})
            values = series.get("values")
            if values is None and result_type == "vector":
                values = [series.get("value")]
            if not isinstance(labels, dict) or not isinstance(values, list):
                raise ProviderExecutionError("invalid_response", "Loki series values are invalid")
            for value in values:
                if not isinstance(value, list) or len(value) != 2 or not isinstance(value[1], str):
                    raise ProviderExecutionError("invalid_response", "Loki sample is invalid")
                timestamp = str(value[0])
                if not timestamp.replace(".", "", 1).isdigit():
                    raise ProviderExecutionError("invalid_response", "Loki timestamp is invalid")
                records.append({"timestamp": timestamp, "labels": labels, "value": value[1]})
        records.sort(
            key=lambda item: (
                float(item["timestamp"]),
                json.dumps(item["labels"], sort_keys=True),
                item["value"],
            )
        )
        return {
            "provider": "loki",
            "result_type": result_type,
            "records": records[:limit],
            "record_count": min(len(records), limit),
            "truncated": len(records) > limit,
            "statistics": data.get("stats", {}),
        }
