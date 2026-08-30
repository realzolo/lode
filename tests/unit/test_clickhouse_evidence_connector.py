from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Any

import pytest

import lode.evidence_access.orchestrator as orchestrator_module
from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_connectors.clickhouse import (
    ClickHouseBackend,
    ClickHouseConnector,
    ClickHouseConnectorConfig,
)
from lode.evidence_connectors.types import IntrospectionBudget


def config(**overrides: Any) -> dict[str, Any]:
    value = {
        "host": "clickhouse.example.test",
        "port": 8443,
        "database": "analytics",
        "username": "lode_reader",
        "tls_mode": "verify_full",
    }
    value.update(overrides)
    return value


def action() -> dict[str, Any]:
    return {
        "adapter_kind": "clickhouse_sql",
        "dialect": "clickhouse",
        "execution_mode": "select",
        "query": "SELECT id FROM analytics.events WHERE tenant = 'orders' LIMIT 2",
        "row_limit": 2,
        "timeout_ms": 5_000,
        "output_bytes": 10_000,
        "max_estimated_rows": 100_000,
        "max_estimated_cost": 100_000,
    }


def permit(value: Mapping[str, Any]) -> ExecutionPermit:
    return ExecutionPermit(
        authorized_read_id=1,
        investigation_id=2,
        action=value,
        effective_action_hash="a" * 64,
        _authority=orchestrator_module._PERMIT_AUTHORITY,
    )


class FakeClickHouseBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def attest(self, timeout_ms: int) -> Mapping[str, Any]:
        self.calls.append(("attest", timeout_ms))
        return {"version": "26.3.25.2", "database": "analytics", "readonly": 2}

    async def introspect(
        self,
        allowed_schemas: Sequence[str] | None,
        max_tables: int,
        timeout_ms: int,
    ) -> Mapping[str, Mapping[str, Any]]:
        self.calls.append(("introspect", (allowed_schemas, max_tables, timeout_ms)))
        return {
            "analytics.events": {
                "engine": "MergeTree",
                "columns": {
                    "id": {"type": "UInt64", "nullable": False},
                    "occurred_at": {"type": "DateTime64(3)", "nullable": False},
                    "tenant": {"type": "String", "nullable": False},
                },
            },
            "analytics.snapshot_view": {
                "engine": "View",
                "columns": {
                    "name": {"type": "String", "nullable": False},
                    "state": {"type": "String", "nullable": False},
                },
            },
        }

    async def explain(self, query: str, timeout_ms: int) -> Mapping[str, Any]:
        self.calls.append(("explain", query))
        return {"estimated_rows": 10, "estimated_cost": 4}

    async def fetch(
        self, query: str, row_limit: int, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        self.calls.append(("fetch", (query, row_limit, timeout_ms)))
        return [
            {
                "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
                "ip": IPv4Address("192.0.2.1"),
                "payload": b"ok",
                "attributes": {1: "one"},
                "captured_at": datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
            }
        ]


class FakeDriverResult:
    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        self.column_names = tuple(columns)
        self.result_rows = tuple(tuple(row) for row in rows)


class CatalogClient:
    def __init__(
        self, table_rows: Sequence[Sequence[Any]], readable_names: set[str] | None = None
    ) -> None:
        self.table_rows = tuple(sorted(table_rows))
        self.readable_names = readable_names
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    async def query(
        self, query: str, *, parameters: Mapping[str, Any] | None = None, **_: Any
    ) -> FakeDriverResult:
        self.calls.append((query, parameters))
        if "FROM system.tables" in query:
            assert parameters is not None
            after = parameters["after"]
            limit = parameters["limit"]
            assert isinstance(after, str)
            assert isinstance(limit, int)
            return FakeDriverResult(
                ("table_name", "engine"),
                [row for row in self.table_rows if row[0] > after][:limit],
            )
        if "FROM system.columns" in query:
            assert parameters is not None
            tables = parameters["tables"]
            assert isinstance(tables, list)
            rows = []
            for table in tables:
                rows.extend(
                    [
                        (table, "id", "UInt64", 1),
                        (table, "occurred_at", "DateTime64(3)", 2),
                    ]
                )
            return FakeDriverResult(
                ("table_name", "column_name", "data_type", "position"),
                rows,
            )
        if "CHECK GRANT" in query:
            name = query.rsplit(".", 1)[-1].strip("`")
            allowed = self.readable_names is None or name in self.readable_names
            return FakeDriverResult(("grant",), [(allowed,)])
        raise AssertionError(query)


def bind_catalog_client(monkeypatch, backend: ClickHouseBackend, client: CatalogClient) -> None:
    @asynccontextmanager
    async def fake_client(*_: Any, **__: Any):
        yield client

    monkeypatch.setattr(backend, "_client", fake_client)


@pytest.mark.asyncio
async def test_clickhouse_connector_accepts_broad_readable_catalogs() -> None:
    backend = FakeClickHouseBackend()
    connector = ClickHouseConnector(config(), {"password": "secret"}, backend)

    verified = await connector.verify()
    catalog = await connector.introspect(
        {}, IntrospectionBudget(timeout_ms=3_000, max_resources=500)
    )
    preflight = await connector.preflight(permit(action()))
    result = await connector.execute(permit(action()))

    assert verified.version == "26.3.25.2"
    assert catalog.resources["tables"]["analytics.events"]["time_column"] == "occurred_at"
    assert catalog.resources["tables"]["analytics.snapshot_view"]["time_column"] is None
    assert catalog.resources["tables"]["analytics.snapshot_view"]["stable_order"] == []
    assert catalog.resources["excluded_tables"] == {}
    assert ("introspect", (None, 201, 3_000)) in backend.calls
    assert preflight["estimated_rows"] == 10
    assert result["records"] == [
        {
            "id": "12345678-1234-5678-1234-567812345678",
            "ip": "192.0.2.1",
            "payload": "base64:b2s=",
            "attributes": [{"key": 1, "value": "one"}],
            "captured_at": "2026-08-30T12:00:00+00:00",
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        config(database="system"),
        config(database="INFORMATION_SCHEMA"),
        config(tls_mode="disabled", ca_certificate_pem="-----BEGIN CERTIFICATE-----\ninvalid"),
    ],
)
def test_clickhouse_configuration_rejects_forbidden_scopes_and_tls(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ClickHouseConnector(payload, {"password": "secret"}, FakeClickHouseBackend())


def test_clickhouse_config_supports_explicit_plain_http() -> None:
    value = ClickHouseConnectorConfig.model_validate(config(port=8123, tls_mode="disabled"))
    assert value.tls_mode == "disabled"


@pytest.mark.asyncio
async def test_clickhouse_catalog_uses_object_select_grants(monkeypatch) -> None:
    backend = ClickHouseBackend(ClickHouseConnectorConfig.model_validate(config()), "secret")
    client = CatalogClient(
        [("events", "MergeTree"), ("snapshot_view", "View")], {"events"}
    )
    bind_catalog_client(monkeypatch, backend, client)

    catalog = await backend.introspect(None, 200, 3_000)

    assert catalog == {
        "analytics.events": {
            "engine": "MergeTree",
            "columns": {
                "id": {"type": "UInt64", "nullable": False},
                "occurred_at": {"type": "DateTime64(3)", "nullable": False},
            },
        }
    }
    column_call = next(call for call in client.calls if "FROM system.columns" in call[0])
    assert column_call[1] == {"database": "analytics", "tables": ["events", "snapshot_view"]}
    assert sum("CHECK GRANT SELECT ON" in query for query, _ in client.calls) == 2


@pytest.mark.asyncio
async def test_clickhouse_catalog_returns_over_cap_after_object_grant_checks(monkeypatch) -> None:
    backend = ClickHouseBackend(ClickHouseConnectorConfig.model_validate(config()), "secret")
    client = CatalogClient([(f"table_{index}", "MergeTree") for index in range(201)])
    bind_catalog_client(monkeypatch, backend, client)

    catalog = await backend.introspect(None, 201, 3_000)

    assert len(catalog) == 201
    assert sum("CHECK GRANT SELECT ON" in query for query, _ in client.calls) == 201


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError("raw timeout"), "provider_timeout"),
        (RuntimeError("raw driver detail"), "provider_unavailable"),
    ],
)
def test_clickhouse_driver_failures_are_sanitized(failure: Exception, expected: str) -> None:
    backend = ClickHouseBackend(ClickHouseConnectorConfig.model_validate(config()), "secret")

    mapped = backend.map_exception(failure, "read")

    assert mapped.code == expected
    assert "raw" not in mapped.reason
