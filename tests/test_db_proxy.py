"""Tests for the provider-neutral fixed database query catalog."""

import pytest

from lode.engine.db_proxy import DisallowedQueryError, approved_query_sql, execute_approved_query


class FakeAdapter:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.verified = False

    async def verify(self, config, secrets, *, timeout):
        self.verified = config["tls"] is True and secrets["password"] == "secret"

    async def execute(self, config, secrets, sql, *, timeout, max_rows):
        self.queries.append(sql)
        return ["email", "value"], [{"email": "a@example.com", "value": 1}]


def test_query_catalog_is_dialect_aware_and_rejects_operator_sql() -> None:
    assert approved_query_sql("postgresql", "sample", "public.orders") == 'SELECT * FROM "public"."orders" ORDER BY ctid LIMIT 100'
    assert approved_query_sql("mysql", "count", "orders.orders") == "SELECT count(*) AS row_count FROM `orders`.`orders`"
    with pytest.raises(DisallowedQueryError):
        approved_query_sql("postgresql", "sql", "public.orders")
    with pytest.raises(DisallowedQueryError):
        approved_query_sql("postgresql", "sample", "public.orders; DROP TABLE users")


@pytest.mark.asyncio
async def test_approved_query_uses_structured_config_and_masks_rows() -> None:
    adapter = FakeAdapter()
    config = {
        "engine": "postgresql", "host": "db.example.com", "port": 5432,
        "database": "app", "username": "readonly", "tls": True,
        "allowed_tables": ["public.orders"], "sensitive_columns": [],
    }
    result = await execute_approved_query(
        config, {"password": "secret"}, table="public.orders",
        operation="sample", adapter=adapter,
    )
    assert adapter.verified is True
    assert adapter.queries == ['SELECT * FROM "public"."orders" ORDER BY ctid LIMIT 100']
    assert result["rows"] == [{"email": "***", "value": 1}]
    with pytest.raises(DisallowedQueryError):
        await execute_approved_query(
            config, {"password": "secret"}, table="public.users",
            operation="sample", adapter=adapter,
        )
