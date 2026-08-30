"""Bounded ClickHouse HTTP evidence connector."""

from __future__ import annotations

import os
import ssl
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver import exceptions as clickhouse_errors
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lode.evidence_connectors.sql import (
    SQLBackend,
    SQLConnectorMechanics,
    database_ssl_context,
)
from lode.evidence_connectors.types import ProviderExecutionError

_SYSTEM_DATABASES = {"system", "information_schema"}
_MAX_READ_ROWS = 100_000
_MAX_READ_BYTES = 64 * 1024 * 1024
_MAX_MEMORY_BYTES = 256 * 1024 * 1024
_MAX_THREADS = 4
_CATALOG_PAGE_SIZE = 200


class ClickHouseConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65_535)
    database: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    tls_mode: str = Field(pattern=r"^(verify_full|require|disabled)$")
    ca_certificate_pem: str | None = Field(default=None, min_length=1, max_length=64_000)

    @field_validator("database", "username")
    @classmethod
    def connection_name_is_safe(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError(
                "database connection names must not contain surrounding whitespace "
                "or control characters"
            )
        return value

    @model_validator(mode="after")
    def tls_configuration_is_valid(self):
        if self.tls_mode == "disabled":
            if self.ca_certificate_pem is not None:
                raise ValueError("database CA certificate requires TLS verification")
            return self
        database_ssl_context(self.tls_mode, self.ca_certificate_pem)
        return self


class ClickHouseBackend(SQLBackend):
    def __init__(self, config: ClickHouseConnectorConfig, password: str) -> None:
        self.config = config
        self.password = password

    async def attest(self, timeout_ms: int) -> Mapping[str, Any]:
        async with self._client(timeout_ms) as client:
            result = await client.query(
                "SELECT version() AS version, currentDatabase() AS database, "
                "getSetting('readonly') AS readonly"
            )
            await client.query("SELECT 1 AS clickhouse_probe ORDER BY ALL LIMIT 1")
        row = self._first_row(result)
        return row

    async def introspect(
        self,
        allowed_schemas: Sequence[str] | None,
        max_tables: int,
        timeout_ms: int,
    ) -> Mapping[str, Mapping[str, Any]]:
        del allowed_schemas
        async with self._client(timeout_ms) as client:
            readable: dict[str, Mapping[str, Any]] = {}
            after = ""
            while True:
                table_result = await client.query(
                    """
                    SELECT name AS table_name, engine
                    FROM system.tables
                    WHERE database = {database:String}
                      AND is_temporary = 0
                      AND ({after:String} = '' OR name > {after:String})
                    ORDER BY name
                    LIMIT {limit:UInt32}
                    """,
                    parameters={
                        "database": self.config.database,
                        "after": after,
                        "limit": _CATALOG_PAGE_SIZE,
                    },
                )
                discovered: dict[str, dict[str, Any]] = {}
                for row in self._rows(table_result):
                    table = row.get("table_name")
                    engine = row.get("engine")
                    if not all(isinstance(value, str) and value for value in (table, engine)):
                        raise ProviderExecutionError("invalid_response", "ClickHouse catalog is invalid")
                    if table in discovered:
                        raise ProviderExecutionError("invalid_response", "ClickHouse catalog is inconsistent")
                    discovered[table] = {"columns": {}, "engine": engine}
                if not discovered:
                    break
                column_result = await client.query(
                    """
                    SELECT table AS table_name, name AS column_name, type AS data_type, position
                    FROM system.columns
                    WHERE database = {database:String}
                      AND table IN {tables:Array(String)}
                    ORDER BY table, position
                    """,
                    parameters={"database": self.config.database, "tables": list(discovered)},
                )
                for row in self._rows(column_result):
                    table = row.get("table_name")
                    column = row.get("column_name")
                    data_type = row.get("data_type")
                    if not all(isinstance(value, str) and value for value in (table, column, data_type)):
                        raise ProviderExecutionError("invalid_response", "ClickHouse catalog is invalid")
                    item = discovered.get(table)
                    if item is None or column in item["columns"]:
                        raise ProviderExecutionError("invalid_response", "ClickHouse catalog is inconsistent")
                    item["columns"][column] = {
                        "type": data_type,
                        "nullable": data_type.lstrip().startswith("Nullable("),
                    }

                for table, descriptor in discovered.items():
                    if not descriptor["columns"]:
                        raise ProviderExecutionError("invalid_response", "ClickHouse catalog is invalid")
                    if await self._can_select(client, table):
                        readable[f"{self.config.database}.{table}"] = descriptor
                        # The shared mechanics asks for 201 entries and rejects
                        # them, preserving the documented 200-object cap.
                        if len(readable) >= max_tables:
                            return readable
                if len(discovered) < _CATALOG_PAGE_SIZE:
                    break
                after = next(reversed(discovered))
        return readable

    async def explain(self, query: str, timeout_ms: int) -> Mapping[str, Any]:
        async with self._client(timeout_ms) as client:
            result = None
            try:
                result = await client.query("EXPLAIN ESTIMATE " + query)
            except clickhouse_errors.DatabaseError:
                # EXPLAIN ESTIMATE applies only to MergeTree-family reads. Validate
                # other engines without executing their data source and rely on the
                # server-side hard limits below during the actual read.
                result = None
            if result is not None and result.result_rows:
                rows = 0.0
                marks = 0.0
                for item in result.result_rows:
                    if len(item) >= 5:
                        rows += float(item[3])
                        marks += float(item[4])
                return {"estimated_rows": rows, "estimated_cost": marks}
            await client.query("EXPLAIN PLAN " + query)
        return {"estimated_rows": _MAX_READ_ROWS, "estimated_cost": _MAX_READ_ROWS}

    async def fetch(
        self, query: str, row_limit: int, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        async with self._client(timeout_ms, row_limit=row_limit) as client:
            result = await client.query(query, settings=self._settings(timeout_ms, row_limit=row_limit))
        return self._rows(result)

    @asynccontextmanager
    async def _client(
        self, timeout_ms: int, *, row_limit: int | None = None
    ) -> AsyncIterator[Any]:
        ca_path: str | None = None
        if self.config.tls_mode == "verify_full" and self.config.ca_certificate_pem:
            descriptor, ca_path = tempfile.mkstemp(prefix="lode-clickhouse-ca-", text=True)
            try:
                os.write(descriptor, self.config.ca_certificate_pem.encode("utf-8"))
            finally:
                os.close(descriptor)
            os.chmod(ca_path, 0o600)
        try:
            is_tls = self.config.tls_mode != "disabled"
            client = await clickhouse_connect.get_async_client(
                host=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.password,
                database=self.config.database,
                interface="https" if is_tls else "http",
                secure=is_tls,
                verify=self.config.tls_mode == "verify_full",
                ca_cert=ca_path,
                connect_timeout=max(1, timeout_ms // 1_000),
                send_receive_timeout=max(1, timeout_ms // 1_000),
                query_retries=0,
                settings=self._settings(timeout_ms, row_limit=row_limit),
            )
            try:
                yield client
            finally:
                await client.close()
        finally:
            if ca_path is not None:
                try:
                    os.unlink(ca_path)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return "`" + value.replace("`", "``") + "`"

    async def _can_select(self, client: Any, table: str) -> bool:
        resource = f"{self._quote_identifier(self.config.database)}.{self._quote_identifier(table)}"
        result = await client.query(f"CHECK GRANT SELECT ON {resource}")
        row = self._first_row(result)
        value = next(iter(row.values()), None)
        return value in {1, "1"}

    @staticmethod
    def _settings(timeout_ms: int, *, row_limit: int | None = None) -> dict[str, int | str]:
        return {
            "readonly": 2,
            "max_execution_time": max(1, (timeout_ms + 999) // 1_000),
            "timeout_before_checking_execution_speed": 0,
            "max_rows_to_read": _MAX_READ_ROWS,
            "max_bytes_to_read": _MAX_READ_BYTES,
            "max_memory_usage": _MAX_MEMORY_BYTES,
            "max_threads": _MAX_THREADS,
            "read_overflow_mode": "throw",
            "result_overflow_mode": "throw",
            **(
                {
                    "max_result_rows": row_limit,
                    "max_result_bytes": _MAX_READ_BYTES,
                }
                if row_limit is not None
                else {}
            ),
        }

    @staticmethod
    def _rows(result: Any) -> list[dict[str, Any]]:
        names = getattr(result, "column_names", None)
        values = getattr(result, "result_rows", None)
        if not isinstance(names, (list, tuple)) or not isinstance(values, (list, tuple)):
            raise ProviderExecutionError("invalid_response", "ClickHouse response is invalid")
        if any(not isinstance(name, str) for name in names):
            raise ProviderExecutionError("invalid_response", "ClickHouse response is invalid")
        output: list[dict[str, Any]] = []
        for row in values:
            if not isinstance(row, (list, tuple)) or len(row) != len(names):
                raise ProviderExecutionError("invalid_response", "ClickHouse response is invalid")
            output.append(dict(zip(names, row, strict=True)))
        return output

    @classmethod
    def _first_row(cls, result: Any) -> dict[str, Any]:
        rows = cls._rows(result)
        if len(rows) != 1:
            raise ProviderExecutionError("invalid_response", "ClickHouse verification is invalid")
        return rows[0]

    @staticmethod
    def map_exception(exc: Exception, phase: str) -> ProviderExecutionError:
        if isinstance(exc, ProviderExecutionError):
            return exc
        code = getattr(exc, "code", None)
        safe_detail = {"clickhouse_code": code} if isinstance(code, int) and 0 <= code <= 10_000 else None
        if isinstance(exc, TimeoutError) or code in {159}:
            return ProviderExecutionError("provider_timeout", f"ClickHouse {phase} timed out.", safe_detail)
        if code in {193, 497, 516}:
            return ProviderExecutionError(
                "authentication_failed", "ClickHouse rejected the configured database or credentials.", safe_detail
            )
        if code in {158, 202, 241}:
            return ProviderExecutionError("cost_exceeded", "ClickHouse read exceeded a server safety limit.", safe_detail)
        if isinstance(exc, ssl.SSLCertVerificationError):
            return ProviderExecutionError(
                "provider_unavailable", "ClickHouse TLS certificate verification failed.", safe_detail
            )
        if isinstance(exc, ssl.SSLError):
            return ProviderExecutionError("provider_unavailable", "ClickHouse TLS negotiation failed.", safe_detail)
        if isinstance(exc, (clickhouse_errors.InterfaceError, OSError)):
            return ProviderExecutionError(
                "provider_unavailable", "ClickHouse host or port is unavailable.", safe_detail
            )
        if isinstance(exc, clickhouse_errors.DatabaseError):
            return ProviderExecutionError(
                "invalid_response", f"ClickHouse rejected the {phase} operation.", safe_detail
            )
        return ProviderExecutionError("provider_unavailable", f"ClickHouse {phase} failed.", safe_detail)


class ClickHouseConnector(SQLConnectorMechanics):
    kind = "clickhouse_sql"
    dialect = "clickhouse"
    allow_non_temporal_tables = True
    allow_unordered_tables = True

    def __init__(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        backend: SQLBackend | None = None,
    ) -> None:
        self.config = ClickHouseConnectorConfig.model_validate(config)
        if self.config.database.lower() in _SYSTEM_DATABASES:
            raise ValueError("ClickHouse system databases cannot be evidence connector scopes")
        if set(secrets) != {"password"} or not secrets["password"]:
            raise ValueError("ClickHouse connector requires one non-empty password")
        super().__init__(backend or ClickHouseBackend(self.config, secrets["password"]), secrets)

    def _validate_attestation(self, attestation: Mapping[str, Any]) -> str:
        version = attestation.get("version")
        database = attestation.get("database")
        readonly = attestation.get("readonly")
        if (
            not isinstance(version, str)
            or not version
            or database != self.config.database
            or readonly not in {1, 2, "1", "2"}
        ):
            raise ProviderExecutionError(
                "authentication_failed",
                "ClickHouse did not honor the configured read-only database session.",
                {"provider": "clickhouse"},
            )
        return version
