"""MySQL read-replica connector and topology/grant attestation."""

from __future__ import annotations

import json
import re
import ssl
from collections.abc import Mapping, Sequence
from typing import Any

import asyncmy
from asyncmy.cursors import DictCursor
from pydantic import BaseModel, ConfigDict, Field

from lode.evidence_connectors.sql import SQLBackend, SQLConnectorMechanics
from lode.evidence_connectors.types import ProviderExecutionError

_GRANT = re.compile(r"^GRANT (?P<privileges>.+) ON (?P<resource>.+) TO ", re.IGNORECASE)


class MySQLConnectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=3306, ge=1, le=65_535)
    database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$-]{0,63}$")
    username: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$-]{0,63}$")


class MySQLBackend(SQLBackend):
    def __init__(self, config: MySQLConnectorConfig, password: str) -> None:
        self.config = config
        self.password = password
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    async def attest(self, timeout_ms: int) -> Mapping[str, Any]:
        async with (
            self._connection(timeout_ms) as connection,
            connection.cursor(DictCursor) as cursor,
        ):
            await cursor.execute(
                "SELECT VERSION() AS version, @@global.read_only AS read_only, "
                "@@global.super_read_only AS super_read_only"
            )
            row = await cursor.fetchone()
            await cursor.execute("SHOW GRANTS FOR CURRENT_USER")
            grant_rows = await cursor.fetchall()
        if not isinstance(row, dict):
            raise ProviderExecutionError(
                "invalid_response", "MySQL topology attestation is missing"
            )
        grants = [
            next(iter(item.values())) for item in grant_rows if isinstance(item, dict) and item
        ]
        return {**row, "grants": grants}

    async def introspect(
        self, max_tables: int, timeout_ms: int
    ) -> Mapping[str, Mapping[str, Any]]:
        output: dict[str, Mapping[str, Any]] = {}
        async with self._connection(timeout_ms) as connection:
            await self._begin_read_only(connection, timeout_ms)
            try:
                async with connection.cursor(DictCursor) as cursor:
                    await cursor.execute(
                        "SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name "
                        "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s "
                        "AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME LIMIT %s",
                        (self.config.database, max_tables),
                    )
                    tables = await cursor.fetchall()
                    for table_row in tables:
                        database, table = table_row["table_schema"], table_row["table_name"]
                        qualified = f"{database}.{table}"
                        await cursor.execute(
                            "SELECT c.COLUMN_NAME AS column_name, c.DATA_TYPE AS data_type, "
                            "c.IS_NULLABLE = 'YES' AS nullable "
                            "FROM information_schema.COLUMNS AS c "
                            "JOIN information_schema.TABLES AS t "
                            "ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
                            "WHERE c.TABLE_SCHEMA = %s AND c.TABLE_NAME = %s "
                            "AND t.TABLE_TYPE = 'BASE TABLE' ORDER BY c.ORDINAL_POSITION",
                            (database, table),
                        )
                        rows = await cursor.fetchall()
                        columns = {
                            row["column_name"]: {
                                "type": row["data_type"],
                                "nullable": bool(row["nullable"]),
                            }
                            for row in rows
                        }
                        await cursor.execute(
                            "SELECT INDEX_NAME AS index_name, NON_UNIQUE AS non_unique, "
                            "COLUMN_NAME AS column_name, SEQ_IN_INDEX AS sequence_number "
                            "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s "
                            "AND TABLE_NAME = %s AND NON_UNIQUE = 0 "
                            "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                            (database, table),
                        )
                        index_rows = await cursor.fetchall()
                        indexes: dict[str, list[str]] = {}
                        for item in index_rows:
                            indexes.setdefault(item["index_name"], []).append(item["column_name"])
                        output[qualified] = {
                            "columns": columns,
                            "primary_key": indexes.pop("PRIMARY", []),
                            "unique_indexes": indexes,
                        }
            finally:
                await connection.rollback()
        return output

    async def explain(self, query: str, timeout_ms: int) -> Mapping[str, Any]:
        async with self._connection(timeout_ms) as connection:
            await self._begin_read_only(connection, timeout_ms)
            try:
                async with connection.cursor(DictCursor) as cursor:
                    await cursor.execute("EXPLAIN FORMAT=JSON " + query)
                    row = await cursor.fetchone()
            finally:
                await connection.rollback()
        if not isinstance(row, dict) or not row:
            raise ProviderExecutionError("invalid_response", "MySQL EXPLAIN is invalid")
        value = next(iter(row.values()))
        try:
            payload = json.loads(value) if isinstance(value, str) else value
            rows, cost = self._estimate(payload)
        except (TypeError, ValueError) as exc:
            raise ProviderExecutionError("invalid_response", "MySQL EXPLAIN is invalid") from exc
        return {"estimated_rows": rows, "estimated_cost": cost}

    async def fetch(
        self, query: str, row_limit: int, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        async with self._connection(timeout_ms) as connection:
            await self._begin_read_only(connection, timeout_ms)
            try:
                async with connection.cursor(DictCursor) as cursor:
                    await cursor.execute(query)
                    rows = await cursor.fetchmany(row_limit)
            finally:
                await connection.rollback()
        return rows

    def _connection(self, timeout_ms: int):
        return _MySQLConnection(self, timeout_ms)

    @staticmethod
    async def _begin_read_only(connection: asyncmy.Connection, timeout_ms: int) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute("SET SESSION TRANSACTION READ ONLY")
            await cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout_ms,))
            await cursor.execute("START TRANSACTION READ ONLY")

    @staticmethod
    def _qualified_table(value: str) -> tuple[str, str]:
        parts = value.split(".")
        if len(parts) != 2 or any(not part for part in parts):
            raise ProviderExecutionError("invalid_response", "MySQL table scope is invalid")
        return parts[0], parts[1]

    @staticmethod
    def _estimate(value: Any) -> tuple[float, float]:
        rows = 0.0
        cost = 0.0
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in {"rows_examined_per_scan", "rows_produced_per_join"}:
                        rows += float(child)
                    elif key == "query_cost":
                        cost = max(cost, float(child))
                    else:
                        stack.append(child)
            elif isinstance(item, list):
                stack.extend(item)
        return rows, cost


class _MySQLConnection:
    def __init__(self, backend: MySQLBackend, timeout_ms: int) -> None:
        self.backend = backend
        self.timeout_ms = timeout_ms
        self.connection: asyncmy.Connection | None = None

    async def __aenter__(self) -> asyncmy.Connection:
        try:
            self.connection = await asyncmy.connect(
                host=self.backend.config.host,
                port=self.backend.config.port,
                user=self.backend.config.username,
                password=self.backend.password,
                database=self.backend.config.database,
                ssl=self.backend.ssl_context,
                autocommit=False,
                local_infile=False,
                connect_timeout=max(1, self.timeout_ms // 1_000),
                read_timeout=max(1, self.timeout_ms // 1_000),
                stmt_cache_size=0,
            )
            return self.connection
        except (TimeoutError, asyncmy.MySQLError) as exc:
            raise ProviderExecutionError(
                "provider_unavailable", "MySQL replica connection failed"
            ) from exc

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.connection is not None:
            await self.connection.ensure_closed()


class MySQLConnector(SQLConnectorMechanics):
    kind = "mysql_sql"
    dialect = "mysql"

    def __init__(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
        backend: SQLBackend | None = None,
    ) -> None:
        self.config = MySQLConnectorConfig.model_validate(config)
        if set(secrets) != {"password"} or not secrets["password"]:
            raise ValueError("MySQL connector requires one non-empty password")
        super().__init__(backend or MySQLBackend(self.config, secrets["password"]), secrets)

    def _validate_attestation(self, attestation: Mapping[str, Any]) -> str:
        version = attestation.get("version")
        grants = attestation.get("grants")
        if (
            not isinstance(version, str)
            or attestation.get("read_only") not in (1, True)
            or attestation.get("super_read_only") not in (1, True)
            or not isinstance(grants, list)
            or not grants
            or any(not self._safe_grant(grant) for grant in grants)
        ):
            raise ProviderExecutionError(
                "authentication_failed", "MySQL endpoint is not an attested read-only replica"
            )
        return version

    def _safe_grant(self, grant: Any) -> bool:
        if not isinstance(grant, str) or "WITH GRANT OPTION" in grant.upper():
            return False
        if grant.upper().startswith("GRANT USAGE ON *.* TO "):
            return True
        match = _GRANT.match(grant)
        if match is None:
            return False
        privileges = {item.strip().upper() for item in match.group("privileges").split(",")}
        resource = match.group("resource").replace("`", "")
        return privileges <= {"SELECT", "SHOW VIEW"} and resource.startswith(
            f"{self.config.database}."
        )
