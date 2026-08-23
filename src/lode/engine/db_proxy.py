"""Read-only database proxy.

Executes analyst-authored SQL against an application's whitelisted, read-only
replica. Every query is validated *before* it reaches the database:

* Only ``SELECT`` (including ``WITH ... SELECT`` common-table-expressions) is
  permitted. Writes, DDL, and transaction-control statements are rejected.
* Every relation referenced must be in the data source's ``allowed_tables``
  allow-list. CTE names and subquery aliases are excluded from the check so
  legitimate CTE usage is not mistaken for a forbidden table.

Sensitive columns (passwords, tokens, emails, …) are masked in the rows that
come back so the read-only replica never leaks PII through the UI.

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
import re
from typing import Any, Protocol, runtime_checkable

import asyncpg

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
# SQL inspection helpers
# ---------------------------------------------------------------------------

# Keywords that terminate a FROM/JOIN item's relation list at the top level
# (i.e. once we hit one of these we are no longer reading table names).
_RELATION_STOPPERS = {
    "where",
    "group",
    "order",
    "having",
    "limit",
    "offset",
    "union",
    "on",
    "using",
    "window",
    "fetch",
    "for",
    "returning",
    "join",
    "from",
    "as",
    "inner",
    "left",
    "right",
    "full",
    "outer",
    "cross",
    "natural",
    "lateral",
    "only",
}

_FROMJOIN_RE = re.compile(r"\b(from|join)\b", re.IGNORECASE)
_RELATION_RE = re.compile(
    r'(?:"?)([A-Za-z_][A-Za-z0-9_]*)(?:"?)'
    r'(?:\.(?:"?)([A-Za-z_][A-Za-z0-9_]*)(?:"?))?'
)
_CTE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.IGNORECASE)


def _strip_sql(sql: str) -> str:
    """Remove comments and single-quoted string literals.

    Stripping strings matters: a literal such as ``'select from orders'`` or a
    stray ``;`` inside a string must not be mistaken for real SQL structure.
    """
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"'(?:[^']|'')*'", " ", sql)
    return sql


def _classify(sql: str) -> str:
    """Return the *main* command of a statement.

    For ``WITH`` queries the CTE block is skipped and the command that follows
    it is reported, so a data-modifying CTE (``WITH ... INSERT``) is correctly
    classified as ``INSERT`` and rejected.
    """
    sql = _strip_sql(sql).strip().rstrip(";").strip()
    if not sql:
        raise DisallowedQueryError("empty statement")
    if ";" in sql:
        raise DisallowedQueryError("multiple statements are not allowed")

    if re.match(r"^\s*\(", sql, re.IGNORECASE):
        # A bare parenthesized subquery — treat as a read.
        return "SELECT"

    if sql[:4].upper() == "WITH":
        depth = 0
        i = 4
        n = len(sql)
        while i < n:
            c = sql[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif depth == 0 and c.isalpha():
                word = sql[i : i + 8]
                m = re.match(r"[A-Za-z]+", word)
                if m:
                    cmd = m.group(0).upper()
                    if cmd in {
                        "SELECT",
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "MERGE",
                        "CREATE",
                        "DROP",
                        "ALTER",
                        "TRUNCATE",
                        "GRANT",
                        "REVOKE",
                    }:
                        return cmd
            i += 1
        return "UNKNOWN"

    m = re.match(r"\s*([A-Za-z]+)", sql)
    return m.group(1).upper() if m else "UNKNOWN"


def _scan_relations(sql: str, start: int) -> tuple[int, set[str]]:
    """Starting just after a FROM/JOIN keyword, collect relation names.

    Handles comma-joined lists (``FROM a, b``), trailing aliases (``FROM a b``),
    and stops at the first clause keyword (``WHERE``, ``ON``, …) so we never
    wander into the predicate.
    """
    tables: set[str] = set()
    i = start
    n = len(sql)
    while i < n:
        while i < n and sql[i].isspace():
            i += 1
        if i >= n:
            break
        ch = sql[i]
        if ch == "(":
            # Subquery / function — not a top-level relation list here.
            break
        if ch == ",":
            i += 1
            continue
        rel = _RELATION_RE.match(sql, i)
        if not rel:
            break
        schema, tbl = rel.group(1), rel.group(2)
        tables.add((tbl or schema).lower())
        i = rel.end()
        # A single trailing identifier is an alias; skip exactly one so it is
        # not mistaken for a second relation.
        j = i
        while j < n and sql[j].isspace():
            j += 1
        if j < n and sql[j] == ",":
            continue
        if j < n:
            wm = re.match(r"[A-Za-z_][A-Za-z0-9_]*", sql[j:])
            if wm and wm.group(0).lower() not in _RELATION_STOPPERS:
                # A single trailing identifier is an alias; skip it and let the
                # outer loop re-detect the next FROM/JOIN clause.
                i = j + len(wm.group(0))
            else:
                i = j
        break
    return i, tables


def _extract_tables(sql: str) -> set[str]:
    sql = _strip_sql(sql)
    tables: set[str] = set()
    i = 0
    n = len(sql)
    while i < n:
        m = _FROMJOIN_RE.search(sql, i)
        if not m:
            break
        i, found = _scan_relations(sql, m.end())
        tables |= found
    return tables


def _extract_cte_names(sql: str) -> set[str]:
    return {m.group(1).lower() for m in _CTE_RE.finditer(_strip_sql(sql))}


def validate_readonly_sql(sql: str, allowed_tables: list[str]) -> dict[str, Any]:
    """Validate ``sql`` against the read-only policy and allow-list.

    Raises :class:`DisallowedQueryError` when the statement is not a read or
    references a table outside ``allowed_tables``. Returns a small summary on
    success.
    """
    command = _classify(sql)
    if command != "SELECT":
        raise DisallowedQueryError(
            f"only SELECT queries are permitted (found {command})"
        )

    cte_names = _extract_cte_names(sql)
    referenced = _extract_tables(sql) - cte_names
    allowed_set = {str(t).lower() for t in (allowed_tables or [])}
    not_allowed = sorted(t for t in referenced if t not in allowed_set)
    if not_allowed:
        raise DisallowedQueryError(
            "tables not in allow-list: " + ", ".join(not_allowed)
        )
    return {"command": "SELECT", "tables": sorted(referenced)}


def resolve_dsn(
    conn_secret_ref: str | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
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

    Structured mode takes precedence when both are present.
    """
    if host:
        return _build_dsn(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
        )
    return _resolve_ref(conn_secret_ref)


def _build_dsn(
    *,
    host: str,
    port: int | None,
    database: str | None,
    username: str | None,
    password: str | None,
) -> str:
    """Assemble a ``postgresql://`` DSN from structured connection fields.

    Userinfo is only included when a username is present, matching libpq
    semantics (an empty password is omitted rather than sent as an empty auth).
    When ``port`` is omitted the standard PostgreSQL port (5432) is assumed.
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
    return f"postgresql://{userinfo}{host}{port_part}{db_part}"


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


def desensitize(columns: list[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mask values in sensitive columns. Returns a new list of row dicts."""
    sensitive_idx = [c for c in columns if _is_sensitive(c)]
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


class AsyncpgConnector:
    """Real connector that talks to PostgreSQL via ``asyncpg``."""

    async def execute(
        self, dsn: str, sql: str, *, timeout: float, max_rows: int
    ) -> tuple[list[str], list[dict[str, Any]]]:
        try:
            conn = await asyncpg.connect(dsn, timeout=timeout)
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
    connector: DbConnector | None = None,
    mask: bool = True,
    timeout: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Validate, resolve, execute, and desensitize a read-only query.

    Connection can be supplied as a legacy ``conn_secret_ref`` or as structured
    fields (``host``/``port``/``database``/``username``/``password``); the two
    are forwarded to :func:`resolve_dsn`.

    Returns a result envelope with ``columns``, ``rows``, ``row_count``,
    ``truncated``, ``desensitized``, and ``allowed_tables``. Propagates
    :class:`DbProxyError` subclasses for policy / source / execution failures.
    """
    validate_readonly_sql(sql, allowed_tables or [])
    dsn = resolve_dsn(
        conn_secret_ref,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
    )
    conn = connector or AsyncpgConnector()
    columns, rows = await conn.execute(dsn, sql, timeout=timeout, max_rows=max_rows)
    truncated = len(rows) >= max_rows
    if mask:
        rows = desensitize(columns, rows)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "desensitized": mask,
        "allowed_tables": [str(t) for t in (allowed_tables or [])],
    }
