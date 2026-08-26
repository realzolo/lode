"""PostgreSQL read-replica connector and topology attestation."""

from __future__ import annotations

import ipaddress
import json
import ssl
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, field_validator

from lode.evidence_connectors.sql import SQLBackend, SQLConnectorMechanics
from lode.evidence_connectors.transport import (
    resolve_checked_addresses,
    validate_dns_hostname,
    validate_ip_cidrs,
)
from lode.evidence_connectors.types import ProviderExecutionError


class PostgreSQLConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65_535)
    database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$-]{0,62}$")
    username: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$-]{0,62}$")
    allowed_ip_cidrs: list[str] = Field(min_length=1, max_length=20)
    ca_certificate_pem: str = Field(min_length=1, max_length=100_000)

    @field_validator("host")
    @classmethod
    def host_is_dns(cls, value: str) -> str:
        return validate_dns_hostname(value)

    @field_validator("allowed_ip_cidrs")
    @classmethod
    def cidrs_are_networks(cls, value: list[str]) -> list[str]:
        return validate_ip_cidrs(value)


class PostgreSQLBackend(SQLBackend):
    def __init__(self, config: PostgreSQLConnectorConfig, password: str) -> None:
        self.config = config
        self.password = password
        try:
            self.ssl_context = ssl.create_default_context(cadata=config.ca_certificate_pem)
        except ssl.SSLError as exc:
            raise ValueError("PostgreSQL CA certificate is invalid") from exc
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.networks = tuple(ipaddress.ip_network(item) for item in config.allowed_ip_cidrs)

    async def attest(self, timeout_ms: int) -> Mapping[str, Any]:
        async with self._connection(timeout_ms) as connection:
            row = await connection.fetchrow(
                """
                SELECT version() AS version,
                       pg_is_in_recovery() AS is_replica,
                       current_setting('transaction_read_only') = 'on' AS transaction_read_only,
                       rol.rolsuper, rol.rolcreaterole, rol.rolcreatedb, rol.rolreplication,
                       pg_has_role(current_user, 'pg_write_all_data', 'MEMBER') AS write_role
                FROM pg_roles AS rol
                WHERE rol.rolname = current_user
                """,
                timeout=timeout_ms / 1_000,
            )
        if row is None:
            raise ProviderExecutionError(
                "invalid_response", "PostgreSQL role attestation is missing"
            )
        return dict(row)

    async def introspect(
        self, tables: Sequence[str], timeout_ms: int
    ) -> Mapping[str, Mapping[str, Any]]:
        output: dict[str, Mapping[str, Any]] = {}
        async with (
            self._connection(timeout_ms) as connection,
            connection.transaction(readonly=True),
        ):
            await self._set_timeouts(connection, timeout_ms)
            for qualified in tables:
                schema, table = self._qualified_table(qualified)
                rows = await connection.fetch(
                    """
                        SELECT c.column_name, c.data_type, c.is_nullable = 'YES' AS nullable
                        FROM information_schema.columns AS c
                        JOIN information_schema.tables AS t
                          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                        WHERE c.table_schema = $1 AND c.table_name = $2
                          AND t.table_type = 'BASE TABLE'
                        ORDER BY c.ordinal_position
                        """,
                    schema,
                    table,
                    timeout=timeout_ms / 1_000,
                )
                output[qualified] = {
                    row["column_name"]: {
                        "type": row["data_type"],
                        "nullable": row["nullable"],
                    }
                    for row in rows
                }
        return output

    async def explain(self, query: str, timeout_ms: int) -> Mapping[str, Any]:
        async with (
            self._connection(timeout_ms) as connection,
            connection.transaction(readonly=True),
        ):
            await self._set_timeouts(connection, timeout_ms)
            value = await connection.fetchval(
                "EXPLAIN (FORMAT JSON) " + query,
                timeout=timeout_ms / 1_000,
            )
        payload = json.loads(value) if isinstance(value, str) else value
        try:
            plan = payload[0]["Plan"]
            return {
                "estimated_rows": float(plan["Plan Rows"]),
                "estimated_cost": float(plan["Total Cost"]),
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderExecutionError(
                "invalid_response", "PostgreSQL EXPLAIN is invalid"
            ) from exc

    async def fetch(
        self, query: str, row_limit: int, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        async with (
            self._connection(timeout_ms) as connection,
            connection.transaction(readonly=True),
        ):
            await self._set_timeouts(connection, timeout_ms)
            rows = await connection.fetch(query, timeout=timeout_ms / 1_000)
        return [dict(row) for row in rows[:row_limit]]

    def _connection(self, timeout_ms: int):
        return _PostgreSQLConnection(self, timeout_ms)

    async def _check_egress(self) -> None:
        await resolve_checked_addresses(self.config.host, self.config.port, self.networks)

    @staticmethod
    async def _set_timeouts(connection: asyncpg.Connection, timeout_ms: int) -> None:
        timeout = str(timeout_ms)
        await connection.execute("SELECT set_config('statement_timeout', $1, true)", timeout)
        await connection.execute("SELECT set_config('lock_timeout', $1, true)", "1000")
        await connection.execute(
            "SELECT set_config('idle_in_transaction_session_timeout', $1, true)", timeout
        )

    @staticmethod
    def _qualified_table(value: str) -> tuple[str, str]:
        parts = value.split(".")
        if len(parts) != 2 or any(not part for part in parts):
            raise ProviderExecutionError("invalid_response", "PostgreSQL table scope is invalid")
        return parts[0], parts[1]


class _PostgreSQLConnection:
    def __init__(self, backend: PostgreSQLBackend, timeout_ms: int) -> None:
        self.backend = backend
        self.timeout_ms = timeout_ms
        self.connection: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        await self.backend._check_egress()
        try:
            self.connection = await asyncpg.connect(
                host=self.backend.config.host,
                port=self.backend.config.port,
                user=self.backend.config.username,
                password=self.backend.password,
                database=self.backend.config.database,
                ssl=self.backend.ssl_context,
                timeout=self.timeout_ms / 1_000,
                command_timeout=self.timeout_ms / 1_000,
                statement_cache_size=0,
                server_settings={"application_name": "lode-evidence-reader"},
            )
            return self.connection
        except (TimeoutError, asyncpg.PostgresConnectionError) as exc:
            raise ProviderExecutionError(
                "provider_unavailable", "PostgreSQL replica connection failed"
            ) from exc

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.connection is not None:
            await self.connection.close(timeout=2)


class PostgreSQLConnector(SQLConnectorMechanics):
    kind = "postgres_sql"
    dialect = "postgres"

    def __init__(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        backend: SQLBackend | None = None,
    ) -> None:
        self.config = PostgreSQLConnectorConfig.model_validate(config)
        if set(secrets) != {"password"} or not secrets["password"]:
            raise ValueError("PostgreSQL connector requires one non-empty password")
        super().__init__(backend or PostgreSQLBackend(self.config, secrets["password"]), secrets)

    def _validate_attestation(self, attestation: Mapping[str, Any]) -> str:
        version = attestation.get("version")
        if (
            not isinstance(version, str)
            or not version.startswith("PostgreSQL ")
            or attestation.get("is_replica") is not True
            or attestation.get("transaction_read_only") is not True
            or any(
                attestation.get(key) is not False
                for key in (
                    "rolsuper",
                    "rolcreaterole",
                    "rolcreatedb",
                    "rolreplication",
                    "write_role",
                )
            )
        ):
            raise ProviderExecutionError(
                "authentication_failed", "PostgreSQL endpoint is not an attested read-only replica"
            )
        return version.split()[1]
