"""Capability-limited PostgreSQL and MySQL evidence queries.

Connections are structured, TLS-verified, and authenticated with decrypted
integration secrets. Callers select only a server-owned operation and an
administrator-approved qualified base table; arbitrary SQL is never accepted.
"""

from __future__ import annotations

import re
import ssl
from typing import Any, Protocol, runtime_checkable

import asyncpg

from lode.runtime_defaults import DATABASE_LOCK_TIMEOUT_SECONDS

DEFAULT_QUERY_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_ROWS = 1000
APPROVED_QUERY_OPERATIONS = frozenset({"sample", "count"})
MASK = "***"
_RELATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE_COLUMN_HINTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_key", "private_key", "ssn", "id_card", "idcard",
    "credit_card", "creditcard", "card_no", "phone", "mobile", "email",
    "address", "birth", "salary",
)


class DbProxyError(Exception):
    status_code = 400


class DisallowedQueryError(DbProxyError):
    pass


class SourceNotResolvableError(DbProxyError):
    status_code = 502


class QueryExecutionError(DbProxyError):
    status_code = 502


def _quote_relation(engine: str, relation: str) -> str:
    if not _RELATION.fullmatch(relation):
        raise DisallowedQueryError("approved table name is invalid")
    quote = '"' if engine == "postgresql" else "`"
    return ".".join(f"{quote}{part}{quote}" for part in relation.split("."))


def approved_query_sql(engine: str, operation: str, table: str) -> str:
    if engine not in {"postgresql", "mysql"}:
        raise DisallowedQueryError("unsupported database engine")
    if operation not in APPROVED_QUERY_OPERATIONS:
        raise DisallowedQueryError("unknown approved query operation")
    relation = _quote_relation(engine, table)
    if operation == "sample":
        order = " ORDER BY ctid" if engine == "postgresql" else ""
        return f"SELECT * FROM {relation}{order} LIMIT 100"
    return f"SELECT count(*) AS row_count FROM {relation}"


def _is_sensitive(column: str) -> bool:
    lowered = column.lower()
    return any(hint in lowered for hint in SENSITIVE_COLUMN_HINTS)


def desensitize(
    columns: list[str],
    rows: list[dict[str, Any]],
    extra_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    extra = {item.lower() for item in (extra_columns or [])}
    sensitive = {item for item in columns if _is_sensitive(item) or item.lower() in extra}
    return [
        {
            key: MASK if key in sensitive and value not in (None, "") else value
            for key, value in row.items()
        }
        for row in rows
    ]


@runtime_checkable
class DatabaseAdapter(Protocol):
    async def verify(
        self, config: dict[str, Any], secrets: dict[str, str], *, timeout: float
    ) -> None: ...

    async def execute(
        self,
        config: dict[str, Any],
        secrets: dict[str, str],
        sql: str,
        *,
        timeout: float,
        max_rows: int,
    ) -> tuple[list[str], list[dict[str, Any]]]: ...


def _tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _pg_kwargs(config: dict[str, Any], secrets: dict[str, str], timeout: float) -> dict[str, Any]:
    lock_ms = int(DATABASE_LOCK_TIMEOUT_SECONDS * 1000)
    return {
        "host": config["host"], "port": config["port"],
        "database": config["database"], "user": config["username"],
        "password": secrets["password"], "ssl": _tls_context(), "timeout": timeout,
        "server_settings": {
            "default_transaction_read_only": "on",
            "statement_timeout": str(int(timeout * 1000)),
            "lock_timeout": str(lock_ms),
        },
    }


class PostgresAdapter:
    async def verify(
        self, config: dict[str, Any], secrets: dict[str, str], *, timeout: float
    ) -> None:
        try:
            conn = await asyncpg.connect(**_pg_kwargs(config, secrets, timeout))
            try:
                row = await conn.fetchrow(
                    """
                    SELECT
                      has_database_privilege(current_user, current_database(), 'CREATE, TEMPORARY') AS database_write,
                      COALESCE(bool_or(has_schema_privilege(current_user, n.oid, 'CREATE')), false) AS schema_create,
                      COALESCE(bool_or(CASE WHEN c.relkind IN ('r','p','v','m','f') THEN
                        has_table_privilege(current_user, c.oid,
                          'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
                        ELSE false END), false) AS table_write,
                      COALESCE(bool_or(CASE WHEN c.relkind = 'S' THEN
                        has_sequence_privilege(current_user, c.oid, 'USAGE, UPDATE')
                        ELSE false END), false) AS sequence_write
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind IN ('r','p','v','m','f','S')
                      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                    """
                )
                approved = await conn.fetchrow(
                    """
                    SELECT COALESCE(bool_and(c.relkind IN ('r','p') AND
                      has_table_privilege(current_user, c.oid, 'SELECT')), false) AS ok
                    FROM unnest($1::text[]) requested(name)
                    LEFT JOIN pg_class c ON c.oid = to_regclass(requested.name)
                    """,
                    config["allowed_tables"],
                )
            finally:
                await conn.close()
        except SourceNotResolvableError:
            raise
        except Exception as exc:
            raise SourceNotResolvableError(f"could not verify PostgreSQL permissions: {exc}") from exc
        if (
            row is None or approved is None or not bool(approved["ok"])
            or any(bool(row[key]) for key in ("database_write", "schema_create", "table_write", "sequence_write"))
        ):
            raise SourceNotResolvableError("PostgreSQL credential is not strictly read-only")

    async def execute(
        self, config: dict[str, Any], secrets: dict[str, str], sql: str, *,
        timeout: float, max_rows: int,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            conn = await asyncpg.connect(**_pg_kwargs(config, secrets, timeout))
            try:
                async with conn.transaction(readonly=True):
                    statement = await conn.prepare(sql)
                    columns = [item.name for item in statement.get_attributes()]
                    raw = await statement.fetch(timeout=timeout)
                return columns, [dict(item) for item in raw[:max_rows]]
            finally:
                await conn.close()
        except Exception as exc:
            raise QueryExecutionError(f"PostgreSQL fixed query failed: {exc}") from exc


def _parse_mysql_grants(grants: list[str], allowed_tables: list[str]) -> None:
    scopes: set[str] = set()
    for grant in grants:
        normalized = " ".join(grant.replace("`", "").split())
        upper = normalized.upper()
        if " WITH GRANT OPTION" in upper or not upper.startswith("GRANT "):
            raise SourceNotResolvableError("MySQL credential has disallowed grants")
        if upper.startswith("GRANT USAGE ON *.* TO "):
            continue
        match = re.match(r"^GRANT ([A-Z, ]+) ON ([A-Za-z0-9_*.-]+) TO ", upper)
        if match is None:
            raise SourceNotResolvableError("MySQL roles and non-table grants are not allowed")
        privileges = {item.strip() for item in match.group(1).split(",")}
        if privileges != {"SELECT"}:
            raise SourceNotResolvableError("MySQL credential has effective write privileges")
        scopes.add(match.group(2).lower())
    for table in allowed_tables:
        database = table.split(".", 1)[0].lower()
        if table.lower() not in scopes and f"{database}.*" not in scopes:
            raise SourceNotResolvableError(f"MySQL credential lacks SELECT on {table}")


async def _mysql_connect(config: dict[str, Any], secrets: dict[str, str], timeout: float):
    try:
        import asyncmy
    except ImportError as exc:  # pragma: no cover
        raise SourceNotResolvableError("asyncmy is required for MySQL integrations") from exc
    try:
        return await asyncmy.connect(
            host=config["host"], port=config["port"], user=config["username"],
            password=secrets["password"], db=config["database"], ssl=_tls_context(),
            connect_timeout=timeout, autocommit=False,
        )
    except Exception as exc:
        raise SourceNotResolvableError(f"could not connect to MySQL: {exc}") from exc


class MySqlAdapter:
    async def verify(
        self, config: dict[str, Any], secrets: dict[str, str], *, timeout: float
    ) -> None:
        conn = await _mysql_connect(config, secrets, timeout)
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
                rows = await cursor.fetchall()
                _parse_mysql_grants(
                    [str(value) for row in rows for value in row], config["allowed_tables"]
                )
                await cursor.execute("SET SESSION TRANSACTION READ ONLY")
                await cursor.execute("SELECT @@SESSION.transaction_read_only")
                state = await cursor.fetchone()
                if state is None or not bool(state[0]):
                    raise SourceNotResolvableError("MySQL read-only transaction mode was not enforced")
        finally:
            conn.close()

    async def execute(
        self, config: dict[str, Any], secrets: dict[str, str], sql: str, *,
        timeout: float, max_rows: int,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        conn = await _mysql_connect(config, secrets, timeout)
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("SET SESSION TRANSACTION READ ONLY")
                await cursor.execute(sql)
                columns = [str(item[0]) for item in (cursor.description or [])]
                raw = await cursor.fetchmany(max_rows)
            return columns, [dict(zip(columns, row, strict=True)) for row in raw]
        except Exception as exc:
            raise QueryExecutionError(f"MySQL fixed query failed: {exc}") from exc
        finally:
            conn.close()


def database_adapter(engine: str) -> DatabaseAdapter:
    if engine == "postgresql":
        return PostgresAdapter()
    if engine == "mysql":
        return MySqlAdapter()
    raise DisallowedQueryError("unsupported database engine")


async def verify_database_readonly(
    config: dict[str, Any], secrets: dict[str, str], *,
    adapter: DatabaseAdapter | None = None, timeout: float = 5.0,
) -> None:
    await (adapter or database_adapter(config["engine"])).verify(
        config, secrets, timeout=timeout
    )


async def execute_approved_query(
    config: dict[str, Any], secrets: dict[str, str], *, table: str, operation: str,
    adapter: DatabaseAdapter | None = None,
    timeout: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    allowed = {str(item) for item in config["allowed_tables"]}
    if table not in allowed:
        raise DisallowedQueryError("table is not in the approved catalog")
    connector = adapter or database_adapter(config["engine"])
    await connector.verify(config, secrets, timeout=min(timeout, 5.0))
    sql = approved_query_sql(config["engine"], operation, table)
    columns, rows = await connector.execute(
        config, secrets, sql, timeout=timeout, max_rows=max_rows
    )
    rows = desensitize(columns, rows, config.get("sensitive_columns"))
    return {
        "columns": columns, "rows": rows, "row_count": len(rows),
        "truncated": operation == "sample" and len(rows) >= 100,
        "desensitized": True, "allowed_tables": sorted(allowed),
        "operation": operation,
    }
