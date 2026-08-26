"""Provider-neutral mechanics for attested read-only SQL replicas."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Awaitable, Protocol, TypeVar

from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_connectors.common import credential_identity_hash
from lode.evidence_connectors.safety import sanitize_evidence
from lode.evidence_connectors.types import (
    IntrospectionBudget,
    NativeSchemaCatalog,
    ProviderExecutionError,
    VerificationResult,
)


class SQLBackend(Protocol):
    async def attest(self, timeout_ms: int) -> Mapping[str, Any]: ...

    async def introspect(
        self, tables: Sequence[str], timeout_ms: int
    ) -> Mapping[str, Mapping[str, Any]]: ...

    async def explain(self, query: str, timeout_ms: int) -> Mapping[str, Any]: ...

    async def fetch(
        self, query: str, row_limit: int, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]: ...


T = TypeVar("T")


class SQLConnectorMechanics:
    kind: str
    language = "sql"
    dialect: str
    read_capabilities = ("bounded_select", "schema_introspection", "cost_explain")

    def __init__(self, backend: SQLBackend, secrets: Mapping[str, str]) -> None:
        self.backend = backend
        self.secrets = dict(secrets)
        self.version: str | None = None

    async def verify(self) -> VerificationResult:
        attestation = await self._backend_call(self.backend.attest(5_000))
        version = self._validate_attestation(attestation)
        self.version = version
        return VerificationResult(
            self.kind,
            version,
            credential_identity_hash(self.secrets),
            self.read_capabilities,
        )

    async def introspect(
        self, scope: Mapping[str, Any], budget: IntrospectionBudget
    ) -> NativeSchemaCatalog:
        tables = scope.get("allowed_tables")
        if (
            not isinstance(tables, list)
            or not tables
            or len(tables) > budget.max_resources
            or any(not isinstance(table, str) for table in tables)
        ):
            raise ProviderExecutionError("invalid_response", "SQL introspection scope is invalid")
        raw = await self._backend_call(self.backend.introspect(tables, budget.timeout_ms))
        if set(raw) != set(tables):
            raise ProviderExecutionError(
                "invalid_response", "SQL introspection did not resolve the exact table scope"
            )
        resources = 0
        catalog: dict[str, Any] = {}
        table_policies = scope.get("table_policies")
        if not isinstance(table_policies, dict) or set(table_policies) != set(tables):
            raise ProviderExecutionError("invalid_response", "SQL table policies are invalid")
        for table in sorted(tables):
            columns = raw[table]
            policy = table_policies[table]
            if not isinstance(columns, Mapping):
                raise ProviderExecutionError("invalid_response", "SQL table catalog is invalid")
            resources += 1 + len(columns)
            if resources > budget.max_resources:
                raise ProviderExecutionError("cost_exceeded", "SQL schema catalog is too large")
            if (
                not columns
                or not isinstance(policy, dict)
                or policy.get("time_column") not in columns
                or not isinstance(policy.get("stable_order"), list)
                or not policy["stable_order"]
                or any(column not in columns for column in policy["stable_order"])
            ):
                raise ProviderExecutionError("invalid_response", "SQL table catalog is invalid")
            catalog[table] = {
                "columns": dict(columns),
                "time_column": policy["time_column"],
                "stable_order": list(policy["stable_order"]),
            }
        return NativeSchemaCatalog(
            provider=self.kind,
            version=self.version or "unverified",
            resources={"dialect": self.dialect, "tables": catalog},
        )

    async def preflight(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        estimate = await self._validated_estimate(action)
        return {
            "provider": self.kind,
            **estimate,
            "version": self.version,
        }

    async def execute(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        if action["execution_mode"] == "explain":
            estimate = await self._validated_estimate(action)
            encoded = json.dumps(estimate, separators=(",", ":"), sort_keys=True).encode()
            if len(encoded) > action["output_bytes"]:
                raise ProviderExecutionError("cost_exceeded", "SQL output byte budget exceeded")
            return {
                "provider": self.kind,
                "records": [estimate],
                "record_count": 1,
                "bytes": len(encoded),
                "secret_categories": [],
                "prompt_injection_detected": False,
            }
        rows = await self._backend_call(
            self.backend.fetch(action["query"], action["row_limit"] + 1, action["timeout_ms"])
        )
        if len(rows) > action["row_limit"]:
            raise ProviderExecutionError("cost_exceeded", "SQL row budget exceeded")
        normalized = [self._normalize_row(row) for row in rows]
        encoded = json.dumps(
            normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        if len(encoded) > action["output_bytes"]:
            raise ProviderExecutionError("cost_exceeded", "SQL output byte budget exceeded")
        sanitized, categories, injection = sanitize_evidence(
            {"provider": self.kind, "records": normalized, "record_count": len(normalized)}
        )
        return {
            **sanitized,
            "bytes": len(encoded),
            "secret_categories": list(categories),
            "prompt_injection_detected": injection,
        }

    def _action(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        if not isinstance(permit, ExecutionPermit):
            raise PermissionError("SQL adapter requires an internal execution permit")
        permit.assert_valid()
        action = permit.action
        required = {
            "adapter_kind",
            "dialect",
            "execution_mode",
            "query",
            "row_limit",
            "timeout_ms",
            "output_bytes",
            "max_estimated_rows",
            "max_estimated_cost",
        }
        if (
            action.get("adapter_kind") != self.kind
            or action.get("dialect") != self.dialect
            or action.get("execution_mode") not in {"select", "explain"}
            or not required <= set(action)
        ):
            raise PermissionError(f"execution permit is not authorized for {self.kind}")
        return action

    async def _validated_estimate(self, action: Mapping[str, Any]) -> Mapping[str, int | float]:
        estimate = await self._backend_call(
            self.backend.explain(action["query"], action["timeout_ms"])
        )
        rows = estimate.get("estimated_rows")
        cost = estimate.get("estimated_cost")
        if (
            isinstance(rows, bool)
            or not isinstance(rows, (int, float))
            or rows < 0
            or isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or cost < 0
        ):
            raise ProviderExecutionError("invalid_response", "SQL EXPLAIN result is invalid")
        if rows > action["max_estimated_rows"] or cost > action["max_estimated_cost"]:
            raise ProviderExecutionError("cost_exceeded", "SQL EXPLAIN budget exceeded")
        return {"estimated_rows": rows, "estimated_cost": cost}

    def _validate_attestation(self, attestation: Mapping[str, Any]) -> str:
        raise NotImplementedError

    @staticmethod
    async def _backend_call(operation: Awaitable[T]) -> T:
        try:
            return await operation
        except ProviderExecutionError:
            raise
        except TimeoutError as exc:
            raise ProviderExecutionError("provider_timeout", "SQL provider timed out") from exc
        except Exception as exc:
            raise ProviderExecutionError(
                "provider_unavailable", "SQL provider operation failed"
            ) from exc

    @staticmethod
    def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(row, Mapping) or any(not isinstance(key, str) for key in row):
            raise ProviderExecutionError("invalid_response", "SQL row shape is invalid")
        return {key: SQLConnectorMechanics._normalize_value(value) for key, value in row.items()}

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (list, tuple)):
            return [SQLConnectorMechanics._normalize_value(item) for item in value]
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise ProviderExecutionError("invalid_response", "SQL object key is invalid")
            return {
                key: SQLConnectorMechanics._normalize_value(item) for key, item in value.items()
            }
        raise ProviderExecutionError("invalid_response", "SQL value type is unsupported")
