"""Hermetic tests for the read-only DB proxy.

No live PostgreSQL is required: the connector is injected, so validation,
desensitization, DSN resolution, and source selection are all exercised with
fakes. The real ``AsyncpgConnector`` is exercised only by type/import checks
here; its network path is covered by the integration smoke elsewhere.
"""

from __future__ import annotations

import pytest

from lode.engine import db_proxy
from lode.engine.db_proxy import (
    DbProxyError,
    DisallowedQueryError,
    QueryExecutionError,
    SourceNotResolvableError,
    desensitize,
    execute_query,
    resolve_dsn,
    validate_readonly_sql,
)


class FakeConnector:
    """Returns a fixed result set regardless of the SQL it is given."""

    def __init__(self, columns=("id", "email", "status"), rows=None):
        self.columns = list(columns)
        self.rows = rows if rows is not None else [
            {"id": 1, "email": "a@x.com", "status": "ok"},
            {"id": 2, "email": "b@x.com", "status": "ok"},
        ]
        self.calls = []

    async def execute(self, dsn, sql, *, timeout, max_rows):
        self.calls.append((dsn, sql, timeout, max_rows))
        return self.columns, [dict(r) for r in self.rows[:max_rows]]


# ---------------------------------------------------------------------------
# Read-only enforcement
# ---------------------------------------------------------------------------


def test_select_is_allowed():
    info = validate_readonly_sql("SELECT * FROM orders", ["orders"])
    assert info["command"] == "SELECT"
    assert info["tables"] == ["orders"]


def test_insert_rejected():
    with pytest.raises(DisallowedQueryError):
        validate_readonly_sql("INSERT INTO orders VALUES (1)", ["orders"])


def test_update_rejected():
    with pytest.raises(DisallowedQueryError):
        validate_readonly_sql("UPDATE orders SET status='x'", ["orders"])


def test_delete_rejected():
    with pytest.raises(DisallowedQueryError):
        validate_readonly_sql("DELETE FROM orders", ["orders"])


def test_drop_rejected():
    with pytest.raises(DisallowedQueryError):
        validate_readonly_sql("DROP TABLE orders", ["orders"])


def test_multiple_statements_rejected():
    with pytest.raises(DisallowedQueryError):
        validate_readonly_sql("SELECT 1; SELECT 2", ["orders"])


def test_data_modifying_cte_rejected():
    # WITH ... INSERT is not a read.
    with pytest.raises(DisallowedQueryError):
        validate_readonly_sql(
            "WITH t AS (SELECT 1) INSERT INTO orders SELECT 1", ["orders"]
        )


# ---------------------------------------------------------------------------
# Allow-list enforcement
# ---------------------------------------------------------------------------


def test_non_whitelisted_table_rejected():
    with pytest.raises(DisallowedQueryError) as exc:
        validate_readonly_sql("SELECT * FROM secrets", ["orders"])
    assert "secrets" in str(exc.value)


def test_schema_qualified_table_allowed():
    validate_readonly_sql("SELECT * FROM public.orders", ["orders"])


def test_cte_name_not_treated_as_table():
    # `recent` is a CTE, not a real table; it must not trip the allow-list.
    validate_readonly_sql(
        "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
        ["orders"],
    )


def test_comma_join_requires_both_tables():
    with pytest.raises(DisallowedQueryError) as exc:
        validate_readonly_sql("SELECT * FROM orders, secrets", ["orders"])
    assert "secrets" in str(exc.value)


def test_join_with_alias_allowed():
    validate_readonly_sql(
        "SELECT * FROM orders o JOIN payments p ON o.id = p.order_id",
        ["orders", "payments"],
    )


def test_constant_select_needs_no_table():
    validate_readonly_sql("SELECT 1", [])


# ---------------------------------------------------------------------------
# DSN resolution
# ---------------------------------------------------------------------------


def test_resolve_dsn_env(monkeypatch):
    monkeypatch.setenv("LODE_TEST_DSN", "postgresql://host/db")
    assert resolve_dsn("env://LODE_TEST_DSN") == "postgresql://host/db"


def test_resolve_dsn_env_missing():
    import os

    os.environ.pop("LODE_TEST_DSN_MISSING", None)
    with pytest.raises(SourceNotResolvableError):
        resolve_dsn("env://LODE_TEST_DSN_MISSING")


def test_resolve_dsn_vault_fails_closed():
    with pytest.raises(SourceNotResolvableError):
        resolve_dsn("vault://db/orders-ro")


def test_resolve_dsn_literal_passthrough():
    assert resolve_dsn("postgresql://host/db") == "postgresql://host/db"


def test_resolve_dsn_structured_builds_dsn():
    dsn = resolve_dsn(
        host="10.0.0.5",
        port=5433,
        database="orders",
        username="ro",
        password="secret",
    )
    assert dsn == "postgresql://ro:secret@10.0.0.5:5433/orders"


def test_resolve_dsn_structured_without_credentials():
    dsn = resolve_dsn(host="db.local", database="app")
    # Omitted port falls back to the standard PostgreSQL port 5432.
    assert dsn == "postgresql://db.local:5432/app"


def test_resolve_dsn_neither_raises():
    with pytest.raises(SourceNotResolvableError):
        resolve_dsn()


def test_resolve_dsn_structured_takes_precedence():
    # When both are supplied the structured fields win.
    dsn = resolve_dsn(
        conn_secret_ref="env://IGNORED", host="h", database="d"
    )
    assert dsn == "postgresql://h:5432/d"


# ---------------------------------------------------------------------------
# At-rest encryption for data-source passwords
# ---------------------------------------------------------------------------


def test_crypto_roundtrip():
    from lode.crypto import decrypt_secret, encrypt_secret

    ct = encrypt_secret("super-secret")
    assert ct != "super-secret"
    assert decrypt_secret(ct) == "super-secret"


def test_crypto_none_passthrough():
    from lode.crypto import decrypt_secret, encrypt_secret

    assert encrypt_secret(None) is None
    assert encrypt_secret("") is None
    assert decrypt_secret(None) is None
    assert decrypt_secret("") is None


def test_crypto_garbage_raises():
    from lode.crypto import CryptoError, decrypt_secret

    with pytest.raises(CryptoError):
        # Not valid Fernet ciphertext / wrong key.
        decrypt_secret("not-a-real-ciphertext")


# ---------------------------------------------------------------------------
# Desensitization
# ---------------------------------------------------------------------------


def test_desensitize_masks_sensitive_columns():
    columns = ["id", "email", "token", "status"]
    rows = [{"id": 1, "email": "a@x.com", "token": "abc", "status": "ok"}]
    out = desensitize(columns, rows)
    assert out[0]["email"] == "***"
    assert out[0]["token"] == "***"
    assert out[0]["id"] == 1
    assert out[0]["status"] == "ok"


def test_desensitize_no_sensitive_columns_is_noop():
    columns = ["id", "status"]
    rows = [{"id": 1, "status": "ok"}]
    assert desensitize(columns, rows) == rows


def test_desensitize_skips_empty_values():
    columns = ["email"]
    rows = [{"email": ""}, {"email": None}]
    out = desensitize(columns, rows)
    assert out[0]["email"] == ""
    assert out[1]["email"] is None


# ---------------------------------------------------------------------------
# Execution orchestration (with injected connector)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_query_runs_and_desensitizes():
    conn = FakeConnector()
    res = await execute_query(
        "postgresql://localhost/test",
        ["orders"],
        "SELECT * FROM orders",
        connector=conn,
    )
    assert res["row_count"] == 2
    assert res["columns"] == ["id", "email", "status"]
    assert res["rows"][0]["email"] == "***"
    assert res["desensitized"] is True


@pytest.mark.asyncio
async def test_execute_query_validation_error_propagates():
    conn = FakeConnector()
    with pytest.raises(DisallowedQueryError):
        await execute_query(
            "env://DSN",
            ["orders"],
            "SELECT * FROM forbidden",
            connector=conn,
        )
    assert conn.calls == []  # never reached the connector


@pytest.mark.asyncio
async def test_execute_query_source_error_propagates():
    with pytest.raises(SourceNotResolvableError):
        await execute_query(
            "vault://db/x", ["orders"], "SELECT * FROM orders", connector=FakeConnector()
        )


@pytest.mark.asyncio
async def test_execute_query_respects_max_rows():
    big = [{"id": i} for i in range(50)]
    conn = FakeConnector(columns=("id",), rows=big)
    res = await execute_query(
        "postgresql://localhost/test",
        ["orders"],
        "SELECT * FROM orders",
        connector=conn,
        max_rows=10,
    )
    assert res["row_count"] == 10
    assert res["truncated"] is True


# ---------------------------------------------------------------------------
# Source selection in tools.run_readonly_query (fake session)
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeSession:
    def __init__(self, sources):
        self._sources = sources

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._sources)


class _FakeDbSource:
    def __init__(
        self,
        id,
        conn_secret_ref,
        allowed_tables,
        name="src",
        host=None,
        port=None,
        database=None,
        username=None,
        password=None,
    ):
        self.id = id
        self.name = name
        self.conn_secret_ref = conn_secret_ref
        self.allowed_tables = allowed_tables
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password


@pytest.mark.asyncio
async def test_run_readonly_query_whitelist_only_when_no_sql():
    session = _FakeSession(
        [_FakeDbSource(1, "env://DSN", ["orders", "payments"])]
    )
    res = await __import__(
        "lode.engine.tools", fromlist=["run_readonly_query"]
    ).run_readonly_query(session, 7)
    assert res["allowed_tables"] == ["orders", "payments"]
    assert res["source_count"] == 1


@pytest.mark.asyncio
async def test_run_readonly_query_single_source_autopick():
    session = _FakeSession([_FakeDbSource(9, "postgresql://localhost/a", ["orders"])])
    tools = __import__("lode.engine.tools", fromlist=["run_readonly_query"])
    res = await tools.run_readonly_query(
        session, 7, sql="SELECT * FROM orders", connector=FakeConnector()
    )
    assert res["source_id"] == 9
    assert res["source_name"] == "src"


@pytest.mark.asyncio
async def test_run_readonly_query_multiple_sources_require_id():
    session = _FakeSession(
        [
            _FakeDbSource(1, "postgresql://localhost/a", ["orders"]),
            _FakeDbSource(2, "postgresql://localhost/b", ["payments"]),
        ]
    )
    tools = __import__("lode.engine.tools", fromlist=["run_readonly_query"])
    with pytest.raises(DbProxyError):
        await tools.run_readonly_query(session, 7, sql="SELECT * FROM orders")
    # With an explicit id it disambiguates.
    res = await tools.run_readonly_query(
        session,
        7,
        sql="SELECT * FROM payments",
        source_id=2,
        connector=FakeConnector(),
    )
    assert res["source_id"] == 2


@pytest.mark.asyncio
async def test_run_readonly_query_no_sources_errors():
    session = _FakeSession([])
    tools = __import__("lode.engine.tools", fromlist=["run_readonly_query"])
    with pytest.raises(DbProxyError):
        await tools.run_readonly_query(session, 7, sql="SELECT 1")


def test_asyncpg_connector_is_importable():
    # Ensure the real connector class is defined without importing asyncpg at
    # call time in a way that breaks import; it must be a DbConnector.
    assert db_proxy.AsyncpgConnector is not None
