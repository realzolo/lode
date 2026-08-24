"""Read-only database proxy.

Executes server-owned query templates against an application's approved,
read-only replica. No caller supplies SQL: selection is limited to a fixed
operation and an administrator-approved schema-qualified base table.

Sensitive columns (passwords, tokens, emails, …) are *always* masked in the
rows that come back, so the read-only replica never leaks PII through the UI.
There is deliberately no opt-out: masking is fail-closed.

The actual connection is resolved from either structured connection fields or
a ``conn_secret_ref``:

* **Structured fields** (``host``/``port``/``database``/``username``/
  ``password``) — assembled into a ``postgresql://`` DSN at query time. This is
  what the admin UI posts when an operator types a connection in directly; the
  password is stored on the ``db_sources`` row (acceptable for a self-hosted
  admin console).
* ``conn_secret_ref`` is an ``env://NAME`` reference. The DSN is held only in
  the worker environment and must require certificate and hostname validation.

Execution goes through a single injectable connector so the validation,
desensitization, and orchestration logic is fully testable without a live
PostgreSQL instance.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlsplit

import asyncpg

from lode.config import settings

# Safety rails applied to every executed query.
DEFAULT_QUERY_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_ROWS = 1000
APPROVED_QUERY_OPERATIONS = frozenset({"sample", "count"})

# Column-name fragments that are always treated as sensitive and masked.
SENSITIVE_COLUMN_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "ssn",
    "id_card",
    "idcard",
    "credit_card",
    "creditcard",
    "card_no",
    "phone",
    "mobile",
    "email",
    "address",
    "birth",
    "salary",
)

MASK = "***"


class DbProxyError(Exception):
    """Base class for proxy failures.

    Subclasses set ``status_code`` so the API layer can map the error onto the
    right HTTP response without inspecting the message.
    """

    status_code: int = 400


class DisallowedQueryError(DbProxyError):
    """The SQL was rejected by the read-only / allow-list policy."""

    status_code = 400


class SourceNotResolvableError(DbProxyError):
    """The data source's ``conn_secret_ref`` could not be resolved to a DSN."""

    status_code = 502


class QueryExecutionError(DbProxyError):
    """The query reached the database but was rejected (syntax / permissions)."""

    status_code = 502


def _quote_relation(relation: str) -> str:
    """Quote an administrator-approved PostgreSQL base-table name."""
    parts = relation.split(".")
    if not 1 <= len(parts) <= 2 or any(
        not part or not part.replace("_", "a").isalnum() for part in parts
    ):
        raise DisallowedQueryError("approved table name is invalid")
    return ".".join(f'"{part}"' for part in parts)


def approved_query_sql(operation: str, table: str) -> str:
    """Return SQL owned by the server-side query catalog only."""
    if operation not in APPROVED_QUERY_OPERATIONS:
        raise DisallowedQueryError("unknown approved query operation")
    relation = _quote_relation(table)
    if operation == "sample":
        return f"SELECT * FROM {relation} ORDER BY ctid LIMIT 100"
    return f"SELECT count(*) AS row_count FROM {relation}"


def resolve_dsn(
    conn_secret_ref: str | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    sslmode: str | None = None,
) -> str:
    """Resolve a data source to a real DSN string.

    Two modes are supported:

    * **Structured** — when ``host`` is set, the DSN is assembled from the
      structured connection fields (``host``/``port``/``database``/
      ``username``/``password``). This is the mode the admin UI uses when an
      operator types a connection in directly.
    * **Secret ref** — when only ``conn_secret_ref`` is supplied, it is
      resolved via :func:`_resolve_ref` from an ``env://NAME`` reference,
      keeping real credentials out of the DB row.

    Structured mode takes precedence when both are present. ``sslmode`` is only
    applied to structured DSNs (secret refs carry their own query string).
    """
    if host:
        return _build_dsn(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            sslmode=sslmode,
        )
    return _resolve_ref(conn_secret_ref)


def _build_dsn(
    *,
    host: str,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
    sslmode: str | None = None,
) -> str:
    """Assemble a ``postgresql://`` DSN from structured connection fields.

    Userinfo is only included when a username is present, matching libpq
    semantics (an empty password is omitted rather than sent as an empty auth).
    When ``port`` is omitted the standard PostgreSQL port (5432) is assumed.
    When ``sslmode`` is supplied it is appended as a query parameter so a
    cross-network link can be forced onto TLS (e.g. ``require`` /
    ``verify-full``) instead of silently falling back to cleartext.
    """
    resolved_port = port or 5432
    port_part = f":{resolved_port}"
    if username:
        # Avoid leaking the password into logs via the DSN string returned to
        # callers; we still need it in the connect call, so build it here.
        userinfo = username
        if password:
            userinfo += f":{password}"
        userinfo += "@"
    else:
        userinfo = ""
    db_part = f"/{database}" if database else ""
    dsn = f"postgresql://{userinfo}{host}{port_part}{db_part}"
    if sslmode:
        dsn += f"?sslmode={sslmode}"
    return dsn


def _resolve_ref(conn_secret_ref: str | None) -> str:
    """Resolve an environment-backed DSN without persisting its value."""
    if not conn_secret_ref:
        raise SourceNotResolvableError(
            "no connection configured: set conn_secret_ref or host"
        )
    if conn_secret_ref.startswith("env://"):
        name = conn_secret_ref[len("env://") :]
        value = os.environ.get(name)
        if not value:
            raise SourceNotResolvableError(
                f"environment variable '{name}' is not set"
            )
        return value
    raise SourceNotResolvableError("conn_secret_ref must be an env://NAME reference")


def _is_sensitive(column: str) -> bool:
    c = column.lower()
    return any(hint in c for hint in SENSITIVE_COLUMN_HINTS)


def desensitize(
    columns: list[str],
    rows: list[dict[str, Any]],
    extra_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Mask values in sensitive columns. Returns a new list of row dicts.

    A column is masked when its name matches a built-in heuristic hint *or* it
    is listed in ``extra_columns`` (per-source operator configuration), so
    application-specific PII columns can be protected without code changes.
    """
    extra = {str(c).lower() for c in (extra_columns or [])}
    sensitive_idx = [
        c for c in columns if _is_sensitive(c) or c.lower() in extra
    ]
    if not sensitive_idx:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        new = dict(row)
        for col in sensitive_idx:
            val = new.get(col)
            if val not in (None, ""):
                new[col] = MASK
        out.append(new)
    return out


# ---------------------------------------------------------------------------
# Connector (injectable for testing)
# ---------------------------------------------------------------------------


@runtime_checkable
class DbConnector(Protocol):
    """Anything able to run a read query and return ``(columns, rows)``."""

    async def execute(
        self, dsn: str, sql: str, *, timeout: float, max_rows: int
    ) -> tuple[list[str], list[dict[str, Any]]]:
        ...


def _read_only_server_settings(timeout: float) -> dict[str, str]:
    """Server settings that enforce read-only + bounded execution."""
    lock_ms = int(getattr(settings, "db_proxy_lock_timeout_seconds", 3.0) * 1000)
    return {
        # Defense in depth: even if validation were bypassed, the session cannot
        # mutate data.
        "default_transaction_read_only": "on",
        # Bound runaway queries at the server.
        "statement_timeout": str(int(timeout * 1000)),
        "lock_timeout": str(lock_ms),
    }


def assert_source_readiness(dsn: str) -> None:
    """Require TLS certificate and hostname verification for every source DSN."""
    try:
        sslmode = parse_qs(urlsplit(dsn).query).get("sslmode", [None])[-1]
    except ValueError as exc:
        raise SourceNotResolvableError("data source DSN is invalid") from exc
    if sslmode != "verify-full":
        raise SourceNotResolvableError(
            "data source requires TLS certificate and hostname verification "
            "(sslmode=verify-full)"
        )


class AsyncpgConnector:
    """Real connector that talks to PostgreSQL via ``asyncpg``.

    Every connection is opened in a **read-only transaction** (defense in depth:
    even a query that slips past AST validation cannot mutate data) and capped by
    a server-side ``LIMIT`` plus ``statement_timeout`` / ``lock_timeout``.
    """

    async def execute(
        self, dsn: str, sql: str, *, timeout: float, max_rows: int
    ) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            conn = await asyncpg.connect(
                dsn, timeout=timeout, server_settings=_read_only_server_settings(timeout)
            )
        except asyncpg.InvalidAuthorizationSpecificationError as exc:
            raise SourceNotResolvableError(
                f"authentication failed for data source: {exc}"
            ) from exc
        except Exception as exc:  # connection refused, timeout, bad DSN
            raise SourceNotResolvableError(
                f"could not connect to data source: {exc}"
            ) from exc
        try:
            async with conn.transaction(readonly=True):
                try:
                    stmt = await conn.prepare(sql)
                except asyncpg.PostgresError as exc:
                    raise QueryExecutionError(
                        f"query rejected by database: {exc}"
                    ) from exc
                except Exception as exc:
                    raise QueryExecutionError(
                        f"failed to prepare query: {exc}"
                    ) from exc
                columns = [attr.name for attr in stmt.get_attributes()]
                try:
                    raw = await stmt.fetch(timeout=timeout)
                except asyncpg.PostgresError as exc:
                    raise QueryExecutionError(f"query failed: {exc}") from exc
            rows = [dict(r) for r in raw[:max_rows]]
            return columns, rows
        finally:
            await conn.close()


async def verify_postgres_readonly_account(
    dsn: str, approved_tables: list[str] | None = None, *, timeout: float = 5.0
) -> None:
    """Prove a PostgreSQL account is safe for the fixed base-table catalog.

    The verifier rejects write privileges, temporary-object creation, writable
    sequences, views/foreign tables, missing SELECT grants, and unqualified
    catalog entries. A client-side read-only transaction is defense in depth,
    never the proof of safety.
    """
    try:
        conn = await asyncpg.connect(dsn, timeout=timeout)
        try:
            row = await conn.fetchrow(
                """
                SELECT
                  has_database_privilege(current_user, current_database(), 'CREATE, TEMPORARY') AS database_write,
                  COALESCE(bool_or(has_schema_privilege(current_user, n.oid, 'CREATE')), false) AS schema_create,
                  COALESCE(bool_or(CASE
                    WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN has_table_privilege(
                      current_user, c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
                    )
                    ELSE false
                  END), false) AS table_write,
                  COALESCE(bool_or(CASE
                    WHEN c.relkind = 'S' THEN has_sequence_privilege(
                      current_user, c.oid, 'USAGE, UPDATE'
                    )
                    ELSE false
                  END), false) AS sequence_write
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                """
            )
            tables = approved_tables or []
            approved = await conn.fetchrow(
                """
                SELECT COALESCE(bool_and(
                    c.relkind IN ('r', 'p')
                    AND has_table_privilege(current_user, c.oid, 'SELECT')
                ), false) AS approved_base_tables
                FROM unnest($1::text[]) AS requested(name)
                LEFT JOIN pg_class c ON c.oid = to_regclass(requested.name)
                """,
                tables,
            )
        finally:
            await conn.close()
    except Exception as exc:
        raise SourceNotResolvableError(
            f"could not verify data source permissions: {exc}"
        ) from exc
    if (
        row is None
        or approved is None
        or not bool(approved["approved_base_tables"])
        or any(bool(row[key]) for key in ("database_write", "schema_create", "table_write", "sequence_write"))
    ):
        raise SourceNotResolvableError("data source credential has effective write privileges")


async def test_connection(dsn: str, *, connector: DbConnector | None = None, timeout: float = 5.0) -> float:
    """Open a connection to ``dsn`` and immediately close it.

    Used by the pre-save "test connection" flow. Returns the round-trip time in
    seconds. Any failure (auth, network, bad DSN, missing environment
    reference) propagates as an exception so the caller can surface it as a
    structured ``{ok: false, error}`` result rather than a 500.
    """
    conn = connector or AsyncpgConnector()
    # The real connector's ``execute`` would run a fixed template; for a pure
    # connectivity
    # check we just open/close. ``_FakeConnector``-style injectables implement
    # ``execute`` too, so we expose a dedicated path here.
    return await _open_and_close(dsn, connector=conn, timeout=timeout)


async def _open_and_close(dsn: str, *, connector: DbConnector, timeout: float) -> float:
    import time

    start = time.monotonic()
    if isinstance(connector, AsyncpgConnector):
        try:
            conn = await asyncpg.connect(
                dsn,
                timeout=timeout,
                server_settings=_read_only_server_settings(timeout),
            )
        except asyncpg.InvalidAuthorizationSpecificationError as exc:
            raise SourceNotResolvableError(
                f"authentication failed for data source: {exc}"
            ) from exc
        except Exception as exc:  # connection refused, timeout, bad DSN
            raise SourceNotResolvableError(
                f"could not connect to data source: {exc}"
            ) from exc
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
    else:
        # Injectable connector: run the harmless probe query through it.
        await connector.execute(dsn, "SELECT 1", timeout=timeout, max_rows=1)
    return time.monotonic() - start


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def execute_approved_query(
    conn_secret_ref: str | None,
    allowed_tables: list[str],
    *,
    table: str,
    operation: str,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    sslmode: str | None = None,
    sensitive_columns: list[str] | None = None,
    connector: DbConnector | None = None,
    timeout: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Execute a server-owned template against a pre-approved base table.

    This is the only public execution API. It never parses or accepts caller
    SQL, preventing function-call or extension side effects by construction.
    """
    if table not in {str(item) for item in allowed_tables}:
        raise DisallowedQueryError("table is not in the approved catalog")
    sql = approved_query_sql(operation, table)
    dsn = resolve_dsn(
        conn_secret_ref, host=host, port=port, database=database,
        username=username, password=password, sslmode=sslmode,
    )
    assert_source_readiness(dsn)
    conn = connector or AsyncpgConnector()
    if isinstance(conn, AsyncpgConnector):
        await verify_postgres_readonly_account(dsn, allowed_tables, timeout=min(timeout, 5.0))
    columns, rows = await conn.execute(dsn, sql, timeout=timeout, max_rows=max_rows)
    rows = desensitize(columns, rows, extra_columns=sensitive_columns)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": operation == "sample" and len(rows) >= 100,
        "desensitized": True,
        "tables": [table],
        "allowed_tables": [str(item) for item in allowed_tables],
        "operation": operation,
    }
