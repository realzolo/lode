"""Read-only database proxy.

Executes analyst-authored SQL against an application's whitelisted, read-only
replica. Every query is validated *before* it reaches the database:

* Only ``SELECT`` (including ``WITH ... SELECT`` common-table-expressions) is
  permitted. Writes, DDL, and transaction-control statements are rejected.
* Every relation referenced must be in the data source's ``allowed_tables``
  allow-list. CTE names and subquery aliases are excluded from the check so
  legitimate CTE usage is not mistaken for a forbidden table.

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
* ``conn_secret_ref`` can still be supplied instead:

  * ``env://NAME`` — read the DSN from the ``NAME`` environment variable. This
    is the recommended form so real credentials never touch the database row.
  * ``vault://...`` — reserved for a future secret-manager backend. It fails
    closed (we refuse to run) until that integration ships.
  * a bare ``postgresql://...`` literal — accepted for local development only
    and logged as a warning, because storing a raw DSN in the row is
    discouraged.

Execution goes through a single injectable connector so the validation,
desensitization, and orchestration logic is fully testable without a live
PostgreSQL instance.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

import asyncpg
import sqlglot
from sqlglot import exp

from lode.config import settings

logger = logging.getLogger("lode.engine.db_proxy")

# Safety rails applied to every executed query.
DEFAULT_QUERY_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_ROWS = 1000

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


# ---------------------------------------------------------------------------
# SQL inspection (PostgreSQL AST)
# ---------------------------------------------------------------------------
#
# Validation is performed by parsing the statement into a real SQL AST with
# ``sqlglot`` (PostgreSQL dialect) instead of brittle regexes. Tables are
# discovered by walking the tree, so they cannot hide in string literals or
# subqueries, and any write/DDL node anywhere in the tree — including a
# data-modifying CTE such as ``WITH t AS (...) INSERT ...`` — is rejected. Only a
# single read-only ``SELECT`` (optionally wrapped in a read-only CTE) is accepted.

# Statement node types that are never allowed: any DML/DDL/transaction-control
# node found anywhere in the tree (including inside a CTE body) rejects the
# query. This is what makes a data-modifying CTE fail closed. Function calls
# (``exp.Call``) are deliberately NOT listed: ``SELECT count(*) FROM t`` is a
# legitimate read and must stay permitted.
_WRITE_DDL_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Comment,
    exp.Command,
    exp.Commit,
    exp.Rollback,
    exp.Use,
    exp.Set,
    exp.Execute,
)

# Top-level node types that constitute a read.
_READ_TYPES = (exp.Select, exp.Union, exp.With, exp.Subquery)


def validate_readonly_sql(sql: str, allowed_tables: list[str]) -> dict[str, Any]:
    """Validate ``sql`` against the read-only policy and allow-list.

    Raises :class:`DisallowedQueryError` when the statement is not a single
    read-only query, references a table outside ``allowed_tables``, or fails to
    parse. Returns a small summary (command kind + referenced tables) on success.
    """
    if not sql or not sql.strip():
        raise DisallowedQueryError("empty statement")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as exc:  # sqlglot raises on syntax errors
        raise DisallowedQueryError(f"could not parse SQL: {exc}") from exc

    if len(statements) != 1:
        raise DisallowedQueryError("multiple statements are not allowed")

    root = statements[0]

    # Reject any write/DDL node anywhere in the tree (covers data-modifying CTEs).
    first_offence = next(iter(root.find_all(*_WRITE_DDL_TYPES)), None)
    if first_offence is not None:
        raise DisallowedQueryError(
            "only read-only queries are permitted "
            f"(found {type(first_offence).__name__})"
        )

    if not isinstance(root, _READ_TYPES):
        raise DisallowedQueryError(
            f"only SELECT queries are permitted (found {type(root).__name__})"
        )

    referenced, cte_names = _collect_tables_and_ctes(root)
    allowed_set = {str(t).lower() for t in (allowed_tables or [])}
    not_allowed = sorted(t for t in referenced if t not in allowed_set)
    if not_allowed:
        raise DisallowedQueryError(
            "tables not in allow-list: " + ", ".join(not_allowed)
        )

    command = "WITH" if cte_names else "SELECT"
    return {"command": command, "tables": sorted(referenced)}


def _collect_tables_and_ctes(expression: exp.Expression) -> tuple[set[str], set[str]]:
    """Return ``(real_tables, cte_names)`` for an AST.

    CTE names are excluded from ``real_tables`` so a CTE reference (e.g.
    ``FROM recent``) is not mistaken for a forbidden table, while the *real*
    tables a CTE reads (e.g. ``FROM orders`` inside the CTE body) are still
    checked against the allow-list.
    """
    cte_names: set[str] = set()
    for cte in expression.find_all(exp.CTE):
        name = cte.alias_or_name
        if name:
            cte_names.add(name.lower())

    tables: set[str] = set()
    for tbl in expression.find_all(exp.Table):
        name = tbl.name
        if not name:
            continue
        if name.lower() in cte_names:
            continue
        tables.add(name.lower())
    return tables, cte_names


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
      resolved via :func:`_resolve_ref` (``env://`` / ``vault://`` / bare
      literal), keeping real credentials out of the DB row.

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
    """Resolve a ``conn_secret_ref`` string to a real DSN (legacy mode)."""
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
    if conn_secret_ref.startswith("vault://"):
        raise SourceNotResolvableError(
            f"vault-backed secret '{conn_secret_ref}' is not supported in this "
            "deployment yet"
        )
    # Bare literal DSN (local dev only).
    logger.warning(
        "conn_secret_ref stores a literal DSN; prefer env:// for production"
    )
    return conn_secret_ref


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


def _wrap_with_limit(sql: str, max_rows: int) -> str:
    """Wrap a validated read in a server-side row cap.

    Guarantees the database returns at most ``max_rows`` rows regardless of what
    the analyst wrote, bounding both the result set and the bytes sent over the
    wire. The inner statement has already been AST-validated as a read-only
    SELECT, so wrapping it as a derived table is always safe.
    """
    body = sql.strip()
    while body.endswith(";"):
        body = body[:-1].strip()
    return f"SELECT * FROM (\n{body}\n) AS _lode_q LIMIT {int(max_rows)}"


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


def assert_source_readiness(*, host: str | None, sslmode: str | None) -> None:
    """Fail closed when a structured source violates deployment readiness.

    When ``settings.db_proxy_require_tls`` is set, any structured (host-based)
    source without ``sslmode`` in ``{require, verify-full}`` is rejected so a
    cross-network link to a production replica cannot downgrade to cleartext.
    """
    if host and getattr(settings, "db_proxy_require_tls", False):
        if sslmode not in ("require", "verify-full"):
            raise SourceNotResolvableError(
                "data source requires TLS (sslmode=require|verify-full) in this "
                "deployment; configure sslmode or disable db_proxy_require_tls"
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


async def test_connection(dsn: str, *, connector: DbConnector | None = None, timeout: float = 5.0) -> float:
    """Open a connection to ``dsn`` and immediately close it.

    Used by the pre-save "test connection" flow. Returns the round-trip time in
    seconds. Any failure (auth, network, bad DSN, unsupported ``vault://``
    reference) propagates as an exception so the caller can surface it as a
    structured ``{ok: false, error}`` result rather than a 500.
    """
    conn = connector or AsyncpgConnector()
    # The real connector's ``execute`` would run SQL; for a pure connectivity
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


async def execute_query(
    conn_secret_ref: str | None = None,
    allowed_tables: list[str] | None = None,
    sql: str = "",
    *,
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
    """Validate, resolve, execute, and desensitize a read-only query.

    Connection can be supplied as a legacy ``conn_secret_ref`` or as structured
    fields (``host``/``port``/``database``/``username``/``password``); the two
    are forwarded to :func:`resolve_dsn`. ``sslmode`` forces TLS for structured
    connections; ``sensitive_columns`` extends column masking beyond the
    built-in heuristic.

    Sensitive columns are **always** masked — there is no opt-out. A single
    read-only PostgreSQL connection is opened (read-only transaction, statement/
    lock timeouts, server-side row cap). Returns an envelope with ``columns``,
    ``rows``, ``row_count``, ``truncated``, ``desensitized`` (always ``True``),
    ``tables`` (referenced), and ``allowed_tables``. Propagates
    :class:`DbProxyError` subclasses for policy / source / execution failures.
    """
    validation = validate_readonly_sql(sql, allowed_tables or [])
    assert_source_readiness(host=host, sslmode=sslmode)
    dsn = resolve_dsn(
        conn_secret_ref,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        sslmode=sslmode,
    )
    conn = connector or AsyncpgConnector()
    # Server-side row cap: guarantees the database returns at most ``max_rows``
    # rows regardless of what the analyst wrote (bounds bytes over the wire).
    capped = _wrap_with_limit(sql, max_rows)
    columns, rows = await conn.execute(dsn, capped, timeout=timeout, max_rows=max_rows)
    truncated = len(rows) >= max_rows
    # Always desensitize: fail-closed, no desensitize=false escape hatch.
    rows = desensitize(columns, rows, extra_columns=sensitive_columns)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "desensitized": True,
        "tables": validation["tables"],
        "allowed_tables": [str(t) for t in (allowed_tables or [])],
    }
