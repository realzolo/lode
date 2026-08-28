from __future__ import annotations

import ssl
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg
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
        self,
        allowed_schemas: Sequence[str] | None,
        max_tables: int,
        timeout_ms: int,
    ) -> Mapping[str, Mapping[str, Any]]:
        self.calls.append(("introspect", (allowed_schemas, max_tables)))
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


class _PostgreSQLTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _PostgreSQLConnectionContext:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return None


class _PostgreSQLIntrospectionConnection:
    def __init__(
        self,
        accessible_schemas: Sequence[str],
        *,
        tables: Sequence[tuple[str, str]] = (),
        write_access: Mapping[str, bool] | None = None,
    ) -> None:
        self.accessible_schemas = accessible_schemas
        self.tables = list(tables)
        self.write_access = dict(
            write_access
            or {
                "table_write": False,
                "column_write": False,
                "sequence_write": False,
                "schema_create": False,
            }
        )
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self, *, readonly: bool):
        assert readonly is True
        return _PostgreSQLTransaction()

    async def execute(self, *_args):
        return None

    async def fetch(self, query: str, *args, **_kwargs):
        self.fetch_calls.append((query, args))
        if "FROM pg_catalog.pg_namespace" in query:
            return [{"schema_name": value} for value in self.accessible_schemas]
        if "FROM pg_catalog.pg_tables" in query:
            return [
                {"schemaname": schema, "tablename": table}
                for schema, table in self.tables
            ]
        if "FROM information_schema.columns" in query:
            return [
                {
                    "schema_name": schema,
                    "table_name": table,
                    "column_name": column,
                    "data_type": data_type,
                    "nullable": False,
                }
                for schema, table in self.tables
                for column, data_type in (("id", "bigint"), ("created_at", "timestamp"))
            ]
        if "FROM pg_catalog.pg_class AS tbl" in query:
            return [
                {
                    "schema_name": schema,
                    "table_name": table,
                    "index_name": f"{table}_pkey",
                    "indisprimary": True,
                    "columns": ["id"],
                }
                for schema, table in self.tables
            ]
        raise AssertionError(f"unexpected introspection query: {query}")

    async def fetchrow(self, query: str, *args, **_kwargs):
        self.fetchrow_calls.append((query, args))
        if "AS table_write" in query:
            return self.write_access
        raise AssertionError(f"unexpected introspection query: {query}")


def config() -> dict[str, Any]:
    return {
        "host": "replica.example.test",
        "port": 5432,
        "database": "analytics",
        "username": "lode_reader",
        "tls_mode": "verify_full",
    }


def system_ca_pem() -> str:
    certificate = ssl.create_default_context().get_ca_certs(binary_form=True)[0]
    return ssl.DER_cert_to_PEM_cert(certificate)


def postgres_attestation(**overrides: Any) -> dict[str, Any]:
    value = {
        "version": "PostgreSQL 16.4",
        "is_replica": True,
        "transaction_read_only": True,
        "rolsuper": False,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolreplication": False,
        "rolbypassrls": False,
        "owns_database": False,
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
        {"allowed_schemas": ["public"]} if kind == "postgres_sql" else {},
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


@pytest.mark.asyncio
async def test_postgresql_introspection_receives_the_frozen_schema_allowlist() -> None:
    backend = FakeSQLBackend(attestation=postgres_attestation())
    connector = PostgreSQLConnector(config(), {"password": "secret"}, backend)

    await connector.introspect(
        {"allowed_schemas": ["billing", "public"]},
        IntrospectionBudget(timeout_ms=3_000, max_resources=10),
    )

    assert ("introspect", (("billing", "public"), 201)) in backend.calls


@pytest.mark.asyncio
async def test_postgresql_introspection_requires_an_explicit_schema_allowlist() -> None:
    connector = PostgreSQLConnector(
        config(), {"password": "secret"}, FakeSQLBackend(attestation=postgres_attestation())
    )

    with pytest.raises(ProviderExecutionError) as error:
        await connector.introspect(
            {}, IntrospectionBudget(timeout_ms=3_000, max_resources=10)
        )

    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_postgresql_backend_parameterizes_every_allowed_schema(monkeypatch) -> None:
    backend = PostgreSQLBackend(PostgreSQLConnectorConfig.model_validate(config()), "secret")
    connection = _PostgreSQLIntrospectionConnection(["billing", "public"])
    monkeypatch.setattr(
        backend,
        "_connection",
        lambda _timeout_ms: _PostgreSQLConnectionContext(connection),
    )

    tables = await backend.introspect(("billing", "public"), 201, 3_000)

    assert tables == {}
    namespace_call, table_call = connection.fetch_calls
    assert namespace_call[1] == (["billing", "public"],)
    assert "schemaname = ANY($1::text[])" in table_call[0]
    assert table_call[1] == (["billing", "public"], 201)
    assert connection.fetchrow_calls[0][1] == (["billing", "public"],)


@pytest.mark.asyncio
async def test_postgresql_backend_batches_catalog_queries_for_all_tables(monkeypatch) -> None:
    backend = PostgreSQLBackend(PostgreSQLConnectorConfig.model_validate(config()), "secret")
    connection = _PostgreSQLIntrospectionConnection(
        ["billing", "public"],
        tables=[("billing", "events"), ("public", "orders")],
    )
    monkeypatch.setattr(
        backend,
        "_connection",
        lambda _timeout_ms: _PostgreSQLConnectionContext(connection),
    )

    tables = await backend.introspect(("billing", "public"), 201, 10_000)

    assert set(tables) == {"billing.events", "public.orders"}
    assert tables["billing.events"]["primary_key"] == ["id"]
    assert tables["public.orders"]["columns"]["created_at"] == {
        "type": "timestamp",
        "nullable": False,
    }
    assert len(connection.fetch_calls) == 4
    column_call, index_call = connection.fetch_calls[2:]
    assert column_call[1] == (["billing", "public"], ["events", "orders"])
    assert index_call[1] == (["billing", "public"], ["events", "orders"])


@pytest.mark.asyncio
async def test_postgresql_backend_rejects_an_inaccessible_allowed_schema(monkeypatch) -> None:
    backend = PostgreSQLBackend(PostgreSQLConnectorConfig.model_validate(config()), "secret")
    connection = _PostgreSQLIntrospectionConnection(["public"])
    monkeypatch.setattr(
        backend,
        "_connection",
        lambda _timeout_ms: _PostgreSQLConnectionContext(connection),
    )

    with pytest.raises(ProviderExecutionError) as error:
        await backend.introspect(("billing", "public"), 201, 3_000)

    assert error.value.code == "authentication_failed"
    assert len(connection.fetch_calls) == 1


@pytest.mark.asyncio
async def test_postgresql_backend_rejects_write_privileges_in_allowed_schemas(
    monkeypatch,
) -> None:
    backend = PostgreSQLBackend(PostgreSQLConnectorConfig.model_validate(config()), "secret")
    connection = _PostgreSQLIntrospectionConnection(
        ["public"],
        write_access={
            "table_write": True,
            "column_write": False,
            "sequence_write": False,
            "schema_create": True,
        },
    )
    monkeypatch.setattr(
        backend,
        "_connection",
        lambda _timeout_ms: _PostgreSQLConnectionContext(connection),
    )

    with pytest.raises(ProviderExecutionError) as error:
        await backend.introspect(("public",), 201, 3_000)

    assert error.value.code == "authentication_failed"
    assert error.value.reason == (
        "The PostgreSQL account has write or object-creation privileges in the "
        "allowed Schema. Use a dedicated SELECT-only account."
    )
    assert error.value.detail == {
        "provider": "postgresql",
        "failed_checks": ["table_write", "schema_create"],
    }


@pytest.mark.parametrize(
    "connector",
    [
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
async def test_sql_connector_rejects_write_capable_identity(connector) -> None:
    with pytest.raises(ProviderExecutionError) as error:
        await connector.verify()
    assert error.value.code == "authentication_failed"


@pytest.mark.asyncio
async def test_postgresql_accepts_a_primary_with_read_only_transaction_and_identity() -> None:
    connector = PostgreSQLConnector(
        config(),
        {"password": "secret"},
        FakeSQLBackend(attestation=postgres_attestation(is_replica=False)),
    )

    verified = await connector.verify()

    assert verified.provider == "postgres_sql"


@pytest.mark.asyncio
async def test_postgresql_attestation_reports_the_failed_read_only_checks() -> None:
    connector = PostgreSQLConnector(
        config(),
        {"password": "secret"},
        FakeSQLBackend(
            attestation=postgres_attestation(
                is_replica=False,
                transaction_read_only=False,
                rolsuper=True,
            )
        ),
    )

    with pytest.raises(ProviderExecutionError) as error:
        await connector.verify()

    assert error.value.reason == (
        "PostgreSQL did not honor the connector's read-only transaction."
    )
    assert error.value.detail == {
        "provider": "postgresql",
        "failed_checks": [
            "read_only_session",
            "non_write_capable_account",
        ],
    }


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
    assert PostgreSQLConnectorConfig.model_validate(
        {**config(), "username": "postgres.project-ref"}
    ).username == "postgres.project-ref"
    assert MySQLConnectorConfig.model_validate(
        {**config(), "username": "service.reader"}
    ).username == "service.reader"

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


def test_sql_backends_accept_a_scoped_custom_ca_without_disabling_verification() -> None:
    ca_certificate_pem = system_ca_pem()
    postgres = PostgreSQLBackend(
        PostgreSQLConnectorConfig.model_validate(
            {**config(), "ca_certificate_pem": ca_certificate_pem}
        ),
        "secret",
    )
    mysql = MySQLBackend(
        MySQLConnectorConfig.model_validate(
            {**config(), "ca_certificate_pem": ca_certificate_pem}
        ),
        "secret",
    )

    for backend in (postgres, mysql):
        assert backend.ssl_context.check_hostname is True
        assert backend.ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert backend.ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_sql_backends_support_explicit_encryption_only_tls_without_plaintext() -> None:
    postgres = PostgreSQLBackend(
        PostgreSQLConnectorConfig.model_validate({**config(), "tls_mode": "require"}),
        "secret",
    )
    mysql = MySQLBackend(
        MySQLConnectorConfig.model_validate({**config(), "tls_mode": "require"}),
        "secret",
    )

    for backend in (postgres, mysql):
        assert backend.ssl_context.check_hostname is False
        assert backend.ssl_context.verify_mode == ssl.CERT_NONE
        assert backend.ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2

    with pytest.raises(ValueError):
        PostgreSQLConnectorConfig.model_validate({**config(), "tls_mode": "disable"})


@pytest.mark.parametrize(
    "config_type", [PostgreSQLConnectorConfig, MySQLConnectorConfig]
)
def test_sql_configs_require_an_explicit_tls_mode(config_type) -> None:
    value = config()
    value.pop("tls_mode")

    with pytest.raises(ValueError):
        config_type.model_validate(value)


@pytest.mark.asyncio
async def test_sql_introspection_rejects_more_than_200_readable_tables() -> None:
    class OversizedBackend(FakeSQLBackend):
        async def introspect(
            self,
            allowed_schemas: Sequence[str] | None,
            max_tables: int,
            timeout_ms: int,
        ):
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
        await connector.introspect(
            {"allowed_schemas": ["public"]},
            IntrospectionBudget(timeout_ms=3_000, max_resources=500),
        )

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


@pytest.mark.parametrize(
    "failure,phase,code,reason",
    [
        (
            asyncpg.InvalidPasswordError("raw password detail"),
            "connection",
            "authentication_failed",
            "PostgreSQL rejected the username or password.",
        ),
        (
            asyncpg.InvalidCatalogNameError("raw database detail"),
            "connection",
            "authentication_failed",
            "The configured PostgreSQL database does not exist or is not accessible "
            "to this account.",
        ),
        (
            asyncpg.InsufficientPrivilegeError("raw privilege detail"),
            "scope discovery",
            "authentication_failed",
            "The PostgreSQL account lacks permission for scope discovery.",
        ),
        (
            asyncpg.TooManyConnectionsError("raw capacity detail"),
            "connection",
            "provider_unavailable",
            "PostgreSQL rejected the connection because its connection limit was reached.",
        ),
        (
            TimeoutError("raw timeout detail"),
            "verification",
            "provider_timeout",
            "PostgreSQL verification timed out.",
        ),
    ],
)
def test_postgresql_driver_failures_have_actionable_safe_messages(
    failure: Exception, phase: str, code: str, reason: str
) -> None:
    backend = PostgreSQLBackend(PostgreSQLConnectorConfig.model_validate(config()), "secret")

    mapped = backend.map_exception(failure, phase)

    assert mapped.code == code
    assert mapped.reason == reason
    assert "raw" not in mapped.reason


def test_postgresql_unknown_driver_error_exposes_only_safe_sqlstate() -> None:
    backend = PostgreSQLBackend(PostgreSQLConnectorConfig.model_validate(config()), "secret")

    mapped = backend.map_exception(
        asyncpg.UndefinedTableError("secret table detail"), "verification"
    )

    assert mapped.code == "invalid_response"
    assert mapped.reason == "PostgreSQL rejected the verification operation."
    assert mapped.detail == {"sqlstate": "42P01"}


@pytest.mark.parametrize(
    "failure,code,reason",
    [
        (
            asyncpg.InternalServerError("raw hosted-pooler detail"),
            "authentication_failed",
            "The PostgreSQL gateway rejected the connection. For a hosted connection "
            "pooler, verify that the host and port match the selected connection mode "
            "and that the username uses the provider-required project or tenant suffix.",
        ),
        (
            asyncpg.ProtocolViolationError("raw protocol detail"),
            "provider_unavailable",
            "PostgreSQL or its connection pooler could not establish a compatible "
            "session. Verify the endpoint type and port.",
        ),
    ],
)
def test_postgresql_connection_sqlstates_have_actionable_safe_messages(
    failure: Exception, code: str, reason: str
) -> None:
    backend = PostgreSQLBackend(PostgreSQLConnectorConfig.model_validate(config()), "secret")

    mapped = backend.map_exception(failure, "connection")

    assert mapped.code == code
    assert mapped.reason == reason
    assert mapped.detail == {"sqlstate": failure.sqlstate}
    assert "raw" not in mapped.reason
