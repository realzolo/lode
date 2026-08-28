"""PostgreSQL connector with transaction and least-privilege attestation."""

from __future__ import annotations

import json
import re
import ssl
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lode.evidence_connectors.sql import (
    SQLBackend,
    SQLConnectorMechanics,
    database_ssl_context,
)
from lode.evidence_connectors.types import ProviderExecutionError

_SQLSTATE = re.compile(r"^[0-9A-Z]{5}$")


class PostgreSQLConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65_535)
    database: str = Field(min_length=1, max_length=63)
    username: str = Field(min_length=1, max_length=63)
    tls_mode: str = Field(pattern=r"^(verify_full|require)$")
    ca_certificate_pem: str | None = Field(default=None, min_length=1, max_length=64_000)

    @field_validator("database", "username")
    @classmethod
    def connection_name_is_safe(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError(
                "database connection names must not contain surrounding whitespace "
                "or control characters"
            )
        return value

    @field_validator("ca_certificate_pem")
    @classmethod
    def ca_certificate_is_valid(cls, value: str | None) -> str | None:
        return value

    @model_validator(mode="after")
    def tls_configuration_is_valid(self):
        database_ssl_context(self.tls_mode, self.ca_certificate_pem)
        return self


class PostgreSQLBackend(SQLBackend):
    def __init__(self, config: PostgreSQLConnectorConfig, password: str) -> None:
        self.config = config
        self.password = password
        self.ssl_context = database_ssl_context(
            config.tls_mode, config.ca_certificate_pem
        )

    async def attest(self, timeout_ms: int) -> Mapping[str, Any]:
        async with (
            self._connection(timeout_ms) as connection,
            connection.transaction(readonly=True),
        ):
            row = await connection.fetchrow(
                """
                SELECT version() AS version,
                       pg_is_in_recovery() AS is_replica,
                       current_setting('transaction_read_only') = 'on' AS transaction_read_only,
                       rol.rolsuper, rol.rolcreaterole, rol.rolcreatedb, rol.rolreplication,
                       rol.rolbypassrls,
                       EXISTS (
                           SELECT 1
                           FROM pg_database AS db
                           WHERE db.datname = current_database() AND db.datdba = rol.oid
                       ) AS owns_database,
                       EXISTS (
                           SELECT 1
                           FROM pg_roles AS write_roles
                           WHERE write_roles.rolname = 'pg_write_all_data'
                             AND pg_has_role(current_user, write_roles.oid, 'MEMBER')
                       ) AS write_role
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
        self,
        allowed_schemas: Sequence[str] | None,
        max_tables: int,
        timeout_ms: int,
    ) -> Mapping[str, Mapping[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        async with (
            self._connection(timeout_ms) as connection,
            connection.transaction(readonly=True),
        ):
            await self._set_timeouts(connection, timeout_ms)
            if not allowed_schemas:
                raise ProviderExecutionError(
                    "invalid_response", "PostgreSQL Schema allowlist is required"
                )
            accessible_schema_rows = await connection.fetch(
                """
                SELECT nspname AS schema_name
                FROM pg_catalog.pg_namespace
                WHERE nspname = ANY($1::text[])
                  AND has_schema_privilege(nspname, 'USAGE')
                ORDER BY nspname
                """,
                list(allowed_schemas),
                timeout=timeout_ms / 1_000,
            )
            accessible_schemas = {row["schema_name"] for row in accessible_schema_rows}
            if accessible_schemas != set(allowed_schemas):
                raise ProviderExecutionError(
                    "authentication_failed",
                    "PostgreSQL schema scope is not accessible",
                )
            write_access = await connection.fetchrow(
                """
                SELECT
                    EXISTS (
                        SELECT 1
                        FROM pg_catalog.pg_class AS cls
                        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
                        WHERE ns.nspname = ANY($1::text[])
                          AND cls.relkind IN ('r', 'p', 'v', 'm', 'f')
                          AND (
                              has_table_privilege(current_user, cls.oid, 'INSERT')
                              OR has_table_privilege(current_user, cls.oid, 'UPDATE')
                              OR has_table_privilege(current_user, cls.oid, 'DELETE')
                              OR has_table_privilege(current_user, cls.oid, 'TRUNCATE')
                              OR has_table_privilege(current_user, cls.oid, 'REFERENCES')
                              OR has_table_privilege(current_user, cls.oid, 'TRIGGER')
                          )
                    ) AS table_write,
                    EXISTS (
                        SELECT 1
                        FROM pg_catalog.pg_class AS cls
                        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
                        WHERE ns.nspname = ANY($1::text[])
                          AND cls.relkind IN ('r', 'p', 'v', 'm', 'f')
                          AND (
                              has_any_column_privilege(current_user, cls.oid, 'INSERT')
                              OR has_any_column_privilege(current_user, cls.oid, 'UPDATE')
                              OR has_any_column_privilege(current_user, cls.oid, 'REFERENCES')
                          )
                    ) AS column_write,
                    EXISTS (
                        SELECT 1
                        FROM pg_catalog.pg_class AS cls
                        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
                        WHERE ns.nspname = ANY($1::text[])
                          AND cls.relkind = 'S'
                          AND has_sequence_privilege(current_user, cls.oid, 'UPDATE')
                    ) AS sequence_write,
                    EXISTS (
                        SELECT 1
                        FROM pg_catalog.pg_namespace AS ns
                        WHERE ns.nspname = ANY($1::text[])
                          AND has_schema_privilege(current_user, ns.oid, 'CREATE')
                    ) AS schema_create
                """,
                list(allowed_schemas),
                timeout=timeout_ms / 1_000,
            )
            if write_access is None:
                raise ProviderExecutionError(
                    "invalid_response", "PostgreSQL privilege attestation is missing"
                )
            failed_privileges = [
                name
                for name in (
                    "table_write",
                    "column_write",
                    "sequence_write",
                    "schema_create",
                )
                if write_access[name] is not False
            ]
            if failed_privileges:
                raise ProviderExecutionError(
                    "authentication_failed",
                    "The PostgreSQL account has write or object-creation privileges in "
                    "the allowed Schema. Use a dedicated SELECT-only account.",
                    {"provider": "postgresql", "failed_checks": failed_privileges},
                )
            tables = await connection.fetch(
                """
                SELECT schemaname, tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = ANY($1::text[])
                  AND has_table_privilege(format('%I.%I', schemaname, tablename), 'SELECT')
                ORDER BY schemaname, tablename
                LIMIT $2
                """,
                list(allowed_schemas),
                max_tables,
                timeout=timeout_ms / 1_000,
            )
            if not tables:
                return output
            table_schemas = [row["schemaname"] for row in tables]
            table_names = [row["tablename"] for row in tables]
            for schema, table in zip(table_schemas, table_names, strict=True):
                output[f"{schema}.{table}"] = {
                    "columns": {},
                    "primary_key": [],
                    "unique_indexes": {},
                }
            column_rows = await connection.fetch(
                """
                WITH selected_tables(schema_name, table_name) AS (
                    SELECT * FROM unnest($1::text[], $2::text[])
                )
                SELECT columns.table_schema AS schema_name,
                       columns.table_name,
                       columns.column_name,
                       columns.data_type,
                       columns.is_nullable = 'YES' AS nullable
                FROM information_schema.columns AS columns
                JOIN selected_tables AS selected
                  ON selected.schema_name = columns.table_schema
                 AND selected.table_name = columns.table_name
                ORDER BY columns.table_schema, columns.table_name, columns.ordinal_position
                """,
                table_schemas,
                table_names,
                timeout=timeout_ms / 1_000,
            )
            for row in column_rows:
                qualified = f"{row['schema_name']}.{row['table_name']}"
                output[qualified]["columns"][row["column_name"]] = {
                    "type": row["data_type"],
                    "nullable": row["nullable"],
                }
            index_rows = await connection.fetch(
                """
                WITH selected_tables(schema_name, table_name) AS (
                    SELECT * FROM unnest($1::text[], $2::text[])
                )
                SELECT ns.nspname AS schema_name,
                       tbl.relname AS table_name,
                       idx.relname AS index_name,
                       ix.indisprimary,
                       array_agg(att.attname ORDER BY key.ordinality) AS columns
                FROM pg_catalog.pg_class AS tbl
                JOIN pg_catalog.pg_namespace AS ns ON ns.oid = tbl.relnamespace
                JOIN selected_tables AS selected
                  ON selected.schema_name = ns.nspname
                 AND selected.table_name = tbl.relname
                JOIN pg_catalog.pg_index AS ix ON ix.indrelid = tbl.oid
                JOIN pg_catalog.pg_class AS idx ON idx.oid = ix.indexrelid
                JOIN unnest(ix.indkey) WITH ORDINALITY AS key(attnum, ordinality)
                  ON key.attnum > 0 AND key.ordinality <= ix.indnkeyatts
                JOIN pg_catalog.pg_attribute AS att
                  ON att.attrelid = tbl.oid AND att.attnum = key.attnum
                WHERE ix.indisunique AND ix.indisvalid
                  AND ix.indpred IS NULL AND ix.indexprs IS NULL
                GROUP BY ns.nspname, tbl.relname, idx.relname, ix.indisprimary
                ORDER BY ns.nspname, tbl.relname, ix.indisprimary DESC, idx.relname
                """,
                table_schemas,
                table_names,
                timeout=timeout_ms / 1_000,
            )
            for row in index_rows:
                qualified = f"{row['schema_name']}.{row['table_name']}"
                columns = list(row["columns"])
                if row["indisprimary"]:
                    output[qualified]["primary_key"] = columns
                else:
                    output[qualified]["unique_indexes"][row["index_name"]] = columns
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

    @staticmethod
    def map_exception(exc: Exception, phase: str) -> ProviderExecutionError:
        if isinstance(exc, ProviderExecutionError):
            return exc
        sqlstate = getattr(exc, "sqlstate", None)
        safe_detail = (
            {"sqlstate": sqlstate}
            if isinstance(sqlstate, str) and _SQLSTATE.fullmatch(sqlstate)
            else None
        )
        if isinstance(exc, asyncpg.InvalidPasswordError):
            return ProviderExecutionError(
                "authentication_failed",
                "PostgreSQL rejected the username or password.",
                safe_detail,
            )
        if isinstance(exc, asyncpg.InvalidCatalogNameError):
            return ProviderExecutionError(
                "authentication_failed",
                "The configured PostgreSQL database does not exist or is not accessible "
                "to this account.",
                safe_detail,
            )
        if isinstance(exc, asyncpg.InvalidAuthorizationSpecificationError):
            return ProviderExecutionError(
                "authentication_failed",
                "PostgreSQL rejected the account credentials.",
                safe_detail,
            )
        if isinstance(exc, asyncpg.InsufficientPrivilegeError):
            return ProviderExecutionError(
                "authentication_failed",
                f"The PostgreSQL account lacks permission for {phase}.",
            )
        if isinstance(exc, (asyncpg.QueryCanceledError, TimeoutError)):
            return ProviderExecutionError(
                "provider_timeout", f"PostgreSQL {phase} timed out."
            )
        if isinstance(exc, ssl.SSLCertVerificationError):
            return ProviderExecutionError(
                "provider_unavailable",
                "PostgreSQL TLS certificate verification failed. The certificate must "
                "match the host and chain to a system-trusted or configured CA. "
                "Provide the provider CA or explicitly choose encryption-only TLS.",
            )
        if isinstance(exc, ssl.SSLError):
            return ProviderExecutionError(
                "provider_unavailable",
                "PostgreSQL TLS negotiation failed. The server must support TLS 1.2 or newer.",
            )
        if isinstance(exc, asyncpg.TooManyConnectionsError):
            return ProviderExecutionError(
                "provider_unavailable",
                "PostgreSQL rejected the connection because its connection limit was reached.",
            )
        if isinstance(exc, asyncpg.CannotConnectNowError):
            return ProviderExecutionError(
                "provider_unavailable", "PostgreSQL is not currently accepting connections."
            )
        if (
            phase == "connection"
            and isinstance(sqlstate, str)
            and sqlstate.startswith("08")
        ):
            return ProviderExecutionError(
                "provider_unavailable",
                "PostgreSQL or its connection pooler could not establish a compatible "
                "session. Verify the endpoint type and port.",
                safe_detail,
            )
        if isinstance(exc, (asyncpg.PostgresConnectionError, OSError)):
            return ProviderExecutionError(
                "provider_unavailable",
                "The PostgreSQL connection was interrupted or the configured host and "
                "port are unavailable.",
            )
        if isinstance(exc, asyncpg.PostgresError):
            if phase == "connection" and sqlstate == "XX000":
                return ProviderExecutionError(
                    "authentication_failed",
                    "The PostgreSQL gateway rejected the connection. For a hosted "
                    "connection pooler, verify that the host and port match the selected "
                    "connection mode and that the username uses the provider-required "
                    "project or tenant suffix.",
                    safe_detail,
                )
            if phase == "connection" and isinstance(sqlstate, str):
                if sqlstate.startswith("28"):
                    return ProviderExecutionError(
                        "authentication_failed",
                        "PostgreSQL rejected the account or its authentication method.",
                        safe_detail,
                    )
                if sqlstate.startswith("08"):
                    return ProviderExecutionError(
                        "provider_unavailable",
                        "PostgreSQL or its connection pooler could not establish a "
                        "compatible session. Verify the endpoint type and port.",
                        safe_detail,
                    )
                if sqlstate.startswith("53"):
                    return ProviderExecutionError(
                        "provider_unavailable",
                        "PostgreSQL or its connection pooler has insufficient capacity "
                        "for a new session.",
                        safe_detail,
                    )
            return ProviderExecutionError(
                "invalid_response",
                f"PostgreSQL rejected the {phase} operation.",
                safe_detail,
            )
        return ProviderExecutionError(
            "provider_unavailable", f"PostgreSQL {phase} failed."
        )

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
        except Exception as exc:
            raise self.backend.map_exception(exc, "connection") from exc

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
        if not isinstance(version, str) or not version.startswith("PostgreSQL "):
            raise ProviderExecutionError(
                "invalid_response",
                "PostgreSQL verification returned an unrecognized server version.",
                {"provider": "postgresql"},
            )

        failed_checks: list[str] = []
        if attestation.get("transaction_read_only") is not True:
            failed_checks.append("read_only_session")
        if any(
            attestation.get(key) is not False
            for key in (
                "rolsuper",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "rolbypassrls",
                "owns_database",
                "write_role",
            )
        ):
            failed_checks.append("non_write_capable_account")
        if failed_checks:
            if "read_only_session" in failed_checks:
                reason = (
                    "PostgreSQL did not honor the connector's read-only transaction."
                )
            else:
                reason = (
                    "The PostgreSQL account has write-capable or administrative privileges. "
                    "Use a dedicated read-only account."
                )
            raise ProviderExecutionError(
                "authentication_failed",
                reason,
                {"provider": "postgresql", "failed_checks": failed_checks},
            )
        return version.split()[1]
