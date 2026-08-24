"""Tests for the fixed PostgreSQL query catalog and permission proof."""

from __future__ import annotations

import pytest

from lode.engine import db_proxy
from lode.engine.db_proxy import (
    DisallowedQueryError,
    SourceNotResolvableError,
    approved_query_sql,
    assert_source_readiness,
    execute_approved_query,
    verify_postgres_readonly_account,
)


class FakeConnector:
    def __init__(self):
        self.queries: list[str] = []

    async def execute(self, _dsn, sql, *, timeout, max_rows):
        self.queries.append(sql)
        return ["email", "value"], [{"email": "a@example.com", "value": 1}]


def test_query_catalog_has_no_operator_sql_and_quotes_identifiers() -> None:
    assert approved_query_sql("sample", "public.orders") == 'SELECT * FROM "public"."orders" ORDER BY ctid LIMIT 100'
    assert approved_query_sql("count", "public.orders") == 'SELECT count(*) AS row_count FROM "public"."orders"'
    with pytest.raises(DisallowedQueryError):
        approved_query_sql("sql", "public.orders")
    with pytest.raises(DisallowedQueryError):
        approved_query_sql("sample", "public.orders; DROP TABLE users")


def test_plaintext_dsn_reference_and_tls_downgrade_are_rejected() -> None:
    with pytest.raises(SourceNotResolvableError, match="env://NAME"):
        db_proxy.resolve_dsn("postgresql://readonly@db/app")
    with pytest.raises(SourceNotResolvableError, match="certificate and hostname"):
        assert_source_readiness("postgresql://readonly@db/app?sslmode=require")
    assert_source_readiness("postgresql://readonly@db/app?sslmode=verify-full")


@pytest.mark.asyncio
async def test_approved_query_only_runs_catalog_sql_and_masks_rows(monkeypatch) -> None:
    monkeypatch.setenv(
        "TEST_READONLY_DSN", "postgresql://readonly@db/app?sslmode=verify-full"
    )
    connector = FakeConnector()
    result = await execute_approved_query(
        "env://TEST_READONLY_DSN", ["public.orders"], table="public.orders",
        operation="sample", connector=connector,
    )
    assert connector.queries == ['SELECT * FROM "public"."orders" ORDER BY ctid LIMIT 100']
    assert result["rows"] == [{"email": "***", "value": 1}]
    with pytest.raises(DisallowedQueryError):
        await execute_approved_query(
            "postgresql://readonly@db/app", ["public.orders"], table="public.users",
            operation="sample", connector=connector,
        )


class _PrivilegeConn:
    def __init__(self, privilege_row, approved_row):
        self.privilege_row = privilege_row
        self.approved_row = approved_row
        self.closed = False

    async def fetchrow(self, _sql, *args):
        return self.approved_row if args else self.privilege_row

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_postgres_verifier_rejects_any_write_or_temp_privilege(monkeypatch) -> None:
    conn = _PrivilegeConn(
        {"database_write": True, "schema_create": False, "table_write": False, "sequence_write": False},
        {"approved_base_tables": True},
    )

    async def connect(*_args, **_kwargs):
        return conn

    monkeypatch.setattr(db_proxy.asyncpg, "connect", connect)
    with pytest.raises(SourceNotResolvableError, match="write privileges"):
        await verify_postgres_readonly_account("postgresql://readonly@db/app", ["public.orders"])
    assert conn.closed is True


@pytest.mark.asyncio
async def test_postgres_verifier_rejects_non_base_or_unreadable_catalog_relation(monkeypatch) -> None:
    conn = _PrivilegeConn(
        {"database_write": False, "schema_create": False, "table_write": False, "sequence_write": False},
        {"approved_base_tables": False},
    )

    async def connect(*_args, **_kwargs):
        return conn

    monkeypatch.setattr(db_proxy.asyncpg, "connect", connect)
    with pytest.raises(SourceNotResolvableError):
        await verify_postgres_readonly_account("postgresql://readonly@db/app", ["public.orders"])
