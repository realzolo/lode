from __future__ import annotations

import ssl
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

import lode.evidence_access.orchestrator as orchestrator_module
from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_connectors.mysql import MySQLBackend, MySQLConnector, MySQLConnectorConfig
from lode.evidence_connectors.postgresql import (
    PostgreSQLBackend,
    PostgreSQLConnector,
    PostgreSQLConnectorConfig,
)
from lode.evidence_connectors.types import IntrospectionBudget, ProviderExecutionError


class FakeSQLBackend:
    def __init__(
        self,
        *,
        attestation: Mapping[str, Any],
        estimate: Mapping[str, Any] | None = None,
        rows: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.attestation = attestation
        self.estimate = estimate or {"estimated_rows": 10, "estimated_cost": 20}
        self.rows = rows
        self.calls: list[tuple[str, Any]] = []

    async def attest(self, timeout_ms: int) -> Mapping[str, Any]:
        self.calls.append(("attest", timeout_ms))
        return self.attestation

    async def introspect(
        self, max_tables: int, timeout_ms: int
    ) -> Mapping[str, Mapping[str, Any]]:
        self.calls.append(("introspect", max_tables))
        return {
            "public.orders": {
                "columns": {
                    "id": {"type": "bigint", "nullable": False},
                    "tenant_id": {"type": "text", "nullable": False},
                    "created_at": {"type": "timestamp", "nullable": False},
                },
                "primary_key": ["id"],
                "unique_indexes": {},
            },
            "public.unsafe_events": {
                "columns": {
                    "message": {"type": "text", "nullable": False},
                    "created_at": {"type": "timestamp", "nullable": False},
                },
                "primary_key": [],
                "unique_indexes": {},
            },
        }

    async def explain(self, query: str, timeout_ms: int) -> Mapping[str, Any]:
        self.calls.append(("explain", query))
        return self.estimate

    async def fetch(
        self, query: str, row_limit: int, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        self.calls.append(("fetch", row_limit))
        return self.rows


class FailingSQLBackend(FakeSQLBackend):
    def __init__(self, failure: Exception) -> None:
        super().__init__(attestation=postgres_attestation())
        self.failure = failure

    async def attest(self, timeout_ms: int) -> Mapping[str, Any]:
        raise self.failure


def config() -> dict[str, Any]:
    return {
        "host": "replica.example.test",
        "port": 5432,
        "database": "analytics",
        "username": "lode_reader",
    }


def postgres_attestation(**overrides: Any) -> dict[str, Any]:
    value = {
        "version": "PostgreSQL 16.4",
        "is_replica": True,
        "transaction_read_only": True,
        "rolsuper": False,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolreplication": False,
        "write_role": False,
    }
    value.update(overrides)
    return value


def mysql_attestation(**overrides: Any) -> dict[str, Any]:
    value = {
        "version": "8.4.2",
        "read_only": 1,
        "super_read_only": 1,
        "grants": [
            "GRANT USAGE ON *.* TO `lode_reader`@`%`",
            "GRANT SELECT, SHOW VIEW ON `analytics`.* TO `lode_reader`@`%`",
        ],
    }
    value.update(overrides)
    return value


def action(kind: str, dialect: str) -> dict[str, Any]:
    return {
        "adapter_kind": kind,
        "dialect": dialect,
        "execution_mode": "select",
        "query": "SELECT id FROM public.orders WHERE tenant_id = 'orders' LIMIT 2",
        "row_limit": 2,
        "timeout_ms": 5_000,
        "output_bytes": 10_000,
        "max_estimated_rows": 100,
        "max_estimated_cost": 100,
    }


def permit(value: Mapping[str, Any]) -> ExecutionPermit:
    return ExecutionPermit(
        authorized_read_id=1,
        investigation_id=2,
        action=value,
        effective_action_hash="a" * 64,
        _authority=orchestrator_module._PERMIT_AUTHORITY,
    )


@pytest.mark.parametrize(
    "connector_type,attestation,kind,dialect",
    [
        (PostgreSQLConnector, postgres_attestation(), "postgres_sql", "postgres"),
        (MySQLConnector, mysql_attestation(), "mysql_sql", "mysql"),
    ],
)
@pytest.mark.asyncio
async def test_sql_connector_verify_introspect_preflight_execute(
    connector_type, attestation, kind: str, dialect: str
) -> None:
    backend = FakeSQLBackend(
        attestation=attestation,
        rows=[{"id": 1, "message": "password=secret"}],
    )
    connector = connector_type(config(), {"password": "secret"}, backend)

    verified = await connector.verify()
    catalog = await connector.introspect(
        {},
        IntrospectionBudget(timeout_ms=3_000, max_resources=10),
    )
    preflight = await connector.preflight(permit(action(kind, dialect)))
    result = await connector.execute(permit(action(kind, dialect)))

    assert verified.provider == kind
    assert catalog.resources["tables"]["public.orders"]["time_column"] == "created_at"
    assert catalog.resources["tables"]["public.orders"]["stable_order"] == ["id"]
    assert catalog.resources["excluded_tables"] == {"public.unsafe_events": "no_stable_key"}
    assert preflight["estimated_rows"] == 10
    assert result["records"][0]["message"] == "<REDACTED:credential_assignment>"
    assert result["secret_categories"] == ["credential_assignment"]


@pytest.mark.parametrize(
    "connector",
    [
        PostgreSQLConnector(
            config(),
            {"password": "secret"},
            FakeSQLBackend(attestation=postgres_attestation(is_replica=False)),
        ),
        MySQLConnector(
            config(),
            {"password": "secret"},
            FakeSQLBackend(attestation=mysql_attestation(super_read_only=0)),
        ),
        MySQLConnector(
            config(),
            {"password": "secret"},
            FakeSQLBackend(
                attestation=mysql_attestation(
                    grants=["GRANT SELECT, INSERT ON `analytics`.* TO `lode_reader`@`%`"]
                )
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_sql_connector_rejects_primary_or_write_capable_identity(connector) -> None:
    with pytest.raises(ProviderExecutionError) as error:
        await connector.verify()
    assert error.value.code == "authentication_failed"


@pytest.mark.asyncio
async def test_sql_connector_rejects_explain_and_result_budget_overruns() -> None:
    costly = PostgreSQLConnector(
        config(),
        {"password": "secret"},
        FakeSQLBackend(
            attestation=postgres_attestation(),
            estimate={"estimated_rows": 101, "estimated_cost": 20},
        ),
    )
    with pytest.raises(ProviderExecutionError) as explain_error:
        await costly.preflight(permit(action("postgres_sql", "postgres")))
    assert explain_error.value.code == "cost_exceeded"

    too_many = PostgreSQLConnector(
        config(),
        {"password": "secret"},
        FakeSQLBackend(
            attestation=postgres_attestation(),
            rows=[{"id": 1}, {"id": 2}, {"id": 3}],
        ),
    )
    with pytest.raises(ProviderExecutionError) as row_error:
        await too_many.execute(permit(action("postgres_sql", "postgres")))
    assert row_error.value.code == "cost_exceeded"


@pytest.mark.asyncio
async def test_sql_explain_mode_never_fetches_rows() -> None:
    backend = FakeSQLBackend(attestation=postgres_attestation())
    connector = PostgreSQLConnector(config(), {"password": "secret"}, backend)
    explain_action = {**action("postgres_sql", "postgres"), "execution_mode": "explain"}

    await connector.preflight(permit(explain_action))
    result = await connector.execute(permit(explain_action))

    assert result["records"] == [{"estimated_rows": 10, "estimated_cost": 20}]
    assert [name for name, _ in backend.calls] == ["explain", "explain"]


def test_sql_connector_config_and_permit_are_strict() -> None:
    with pytest.raises(ValueError):
        PostgreSQLConnector(
            {**config(), "ca_certificate_pem": "custom-ca"},
            {"password": "secret"},
            FakeSQLBackend(attestation=postgres_attestation()),
        )

    with pytest.raises(ValueError):
        MySQLConnector(
            {**config(), "allowed_ip_cidrs": ["10.0.0.1/8"]},
            {"password": "secret"},
            FakeSQLBackend(attestation=mysql_attestation()),
        )

    connector = PostgreSQLConnector(
        config(), {"password": "secret"}, FakeSQLBackend(attestation=postgres_attestation())
    )

    class Forged:
        action = action("postgres_sql", "postgres")

        def assert_valid(self) -> None:
            return None

    with pytest.raises(PermissionError):
        connector._action(Forged())


def test_sql_backends_enforce_system_ca_hostname_verification_and_tls_12() -> None:
    postgres = PostgreSQLBackend(
        PostgreSQLConnectorConfig.model_validate(config()), "secret"
    )
    mysql = MySQLBackend(MySQLConnectorConfig.model_validate(config()), "secret")

    for backend in (postgres, mysql):
        assert backend.ssl_context.check_hostname is True
        assert backend.ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert backend.ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2


@pytest.mark.asyncio
async def test_sql_introspection_rejects_more_than_200_readable_tables() -> None:
    class OversizedBackend(FakeSQLBackend):
        async def introspect(self, max_tables: int, timeout_ms: int):
            descriptor = {
                "columns": {
                    "id": {"type": "bigint", "nullable": False},
                    "occurred_at": {"type": "timestamp", "nullable": False},
                },
                "primary_key": ["id"],
                "unique_indexes": {},
            }
            return {f"public.table_{index}": descriptor for index in range(201)}

    connector = PostgreSQLConnector(
        config(), {"password": "secret"}, OversizedBackend(attestation=postgres_attestation())
    )

    with pytest.raises(ProviderExecutionError) as error:
        await connector.introspect({}, IntrospectionBudget(timeout_ms=3_000, max_resources=500))

    assert error.value.code == "cost_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        (TimeoutError(), "provider_timeout"),
        (RuntimeError("driver detail must not escape"), "provider_unavailable"),
    ],
)
async def test_sql_backend_failures_are_mapped_to_stable_codes(
    failure: Exception, code: str
) -> None:
    connector = PostgreSQLConnector(config(), {"password": "secret"}, FailingSQLBackend(failure))
    with pytest.raises(ProviderExecutionError) as error:
        await connector.verify()
    assert error.value.code == code
    assert "driver detail" not in error.value.reason
