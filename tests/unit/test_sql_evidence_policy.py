from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.sql import SQLPolicy
from lode.evidence_access.types import AccessContext, AccessRejection


def candidate(query: str, *, bindings: dict[str, str] | None = None) -> NativeReadCandidateInput:
    return NativeReadCandidateInput.model_validate(
        {
            "schema_version": "native-read-candidate.v1",
            "action_id": "evidence.sql.orders",
            "connector_id": 7,
            "language": "sql",
            "purpose": "find records participating in the incident",
            "expected_evidence": "bounded rows matching the trace",
            "evidence_anchors": ["incident.trace_id"],
            "payload": {"query": query},
            "value_bindings": bindings or {},
            "requested_window": {
                "start": "2026-08-26T09:15:00Z",
                "end": "2026-08-26T09:45:00Z",
            },
            "requested_limit": 200,
            "requested_timeout_ms": 20_000,
        }
    )


def context(*, dialect: str = "postgres", required: bool = True) -> AccessContext:
    tables: dict[str, Any] = {
        "public.orders": {
            "columns": {
                "id": {"type": "bigint"},
                "tenant_id": {"type": "text"},
                "trace_id": {"type": "text"},
                "status": {"type": "text"},
                "created_at": {"type": "timestamp"},
            },
            "time_column": "created_at",
            "stable_order": ["created_at", "id"],
        },
        "public.order_items": {
            "columns": {
                "id": {"type": "bigint"},
                "order_id": {"type": "bigint"},
                "tenant_id": {"type": "text"},
                "created_at": {"type": "timestamp"},
            },
            "time_column": "created_at",
            "stable_order": ["created_at", "id"],
        },
    }
    return AccessContext(
        investigation_id=1,
        operation_id=2,
        connector_snapshot_id=3,
        model_invocation_id=4,
        workspace_id=5,
        connector_id=7,
        snapshot_hash="a" * 64,
        allowed_languages=("sql",),
        allowed_evidence_anchors=("incident.trace_id",),
        scope_config={
            "dialect": dialect,
            "allowed_tables": sorted(tables),
            "required_predicates": (
                {
                    "public.orders": {"tenant_id": "orders"},
                    "public.order_items": {"tenant_id": "orders"},
                }
                if required
                else {}
            ),
            "max_joins": 1,
        },
        schema_catalog={"tables": tables},
        execution_budget_policy={
            "max_result_limit": 50,
            "max_timeout_ms": 5_000,
            "max_output_bytes": 100_000,
            "max_total_output_bytes": 200_000,
            "max_window_seconds": 3_600,
            "max_native_reads": 8,
            "max_parallel_operations": 1,
            "estimated_cost": 0.0,
        },
        investigation_window_start=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
        investigation_window_end=datetime(2026, 8, 26, 10, 0, tzinfo=UTC),
    )


def test_sql_policy_injects_scope_window_order_limit_and_binds_literal() -> None:
    policy = SQLPolicy()
    sentinel = "__LODE_VALUE_REF_INCIDENT_TRACE__"
    raw = candidate(
        f"SELECT o.id, o.status FROM public.orders AS o WHERE o.trace_id = '{sentinel}'",
        bindings={sentinel: "incident.trace_id"},
    )

    parsed = policy.parse(raw)
    evaluated = policy.evaluate(parsed, raw, context())
    bound = policy.bind_values(parsed, evaluated, {sentinel: "quote' ; DROP TABLE orders --"})

    effective = evaluated.effective_action
    assert effective["adapter_kind"] == "postgres_sql"
    assert effective["row_limit"] == 50
    assert effective["timeout_ms"] == 5_000
    assert "o.tenant_id = 'orders'" in effective["query"]
    assert "o.created_at >= '2026-08-26T09:15:00+00:00'" in effective["query"]
    assert "ORDER BY o.created_at, o.id LIMIT 50" in effective["query"]
    assert "quote'' ; DROP TABLE orders --" in bound.canonical_action["query"]
    assert bound.structural_hash == evaluated.effective_structural_hash


def test_sql_policy_allows_bounded_inner_join_and_catalog_columns() -> None:
    policy = SQLPolicy()
    raw = candidate(
        "SELECT o.id, i.id FROM public.orders AS o "
        "INNER JOIN public.order_items AS i ON i.order_id = o.id "
        "WHERE o.status = 'failed' LIMIT 20"
    )
    parsed = policy.parse(raw)
    evaluated = policy.evaluate(parsed, raw, context())

    assert evaluated.effective_action["row_limit"] == 20
    assert "i.created_at >=" in evaluated.effective_action["query"]
    assert evaluated.constraint_diff["mandatory_scope"]["tables"] == [
        "public.orders",
        "public.order_items",
    ]


def test_sql_policy_allows_one_read_only_cte_and_injects_inner_scope() -> None:
    policy = SQLPolicy()
    raw = candidate(
        "WITH recent AS ("
        "SELECT o.id, o.status, o.created_at FROM public.orders AS o "
        "WHERE o.status = 'failed'"
        ") SELECT recent.id, recent.status, recent.created_at FROM recent"
    )

    evaluated = policy.evaluate(policy.parse(raw), raw, context())
    query = evaluated.effective_action["query"]

    assert "o.tenant_id = 'orders'" in query
    assert "o.created_at >= '2026-08-26T09:15:00+00:00'" in query
    assert "ORDER BY recent.created_at, recent.id LIMIT 50" in query


def test_sql_policy_allows_explain_without_authorizing_select_execution() -> None:
    policy = SQLPolicy()
    raw = candidate("EXPLAIN SELECT id FROM public.orders WHERE status = 'failed'")

    evaluated = policy.evaluate(policy.parse(raw), raw, context())

    assert evaluated.effective_action["execution_mode"] == "explain"
    assert not evaluated.effective_action["query"].startswith("EXPLAIN")

    for query in (
        "EXPLAIN ANALYZE SELECT id FROM public.orders WHERE status = 'failed'",
        "EXPLAIN (FORMAT JSON) SELECT id FROM public.orders WHERE status = 'failed'",
    ):
        with pytest.raises(AccessRejection) as error:
            policy.parse(candidate(query))
        assert error.value.code == "unsupported_node"


@pytest.mark.parametrize(
    "query,code",
    [
        ("UPDATE public.orders SET status = 'ok'", "write_semantics"),
        ("SELECT id INTO TEMP x FROM public.orders", "write_semantics"),
        ("SELECT id FROM public.orders FOR UPDATE", "write_semantics"),
        ("SELECT id FROM public.orders; SELECT id FROM public.orders", "invalid_syntax"),
        ("SELECT id FROM public.orders -- suffix", "invalid_syntax"),
        ("SELECT * FROM public.orders WHERE status = 'failed'", "scope_violation"),
        ("SELECT pg_sleep(10) FROM public.orders", "unsupported_node"),
        ("SELECT id FROM information_schema.tables", "scope_violation"),
        ("SELECT secret FROM public.orders", "scope_violation"),
        (
            "SELECT id FROM public.orders WHERE id IN (SELECT id FROM public.orders)",
            "unsupported_node",
        ),
        ("SELECT id FROM public.orders UNION SELECT id FROM public.orders", "write_semantics"),
        (
            "SELECT o.id FROM public.orders o LEFT JOIN public.order_items i ON i.order_id = o.id",
            "unsupported_node",
        ),
    ],
)
def test_sql_policy_rejects_write_unknown_and_unbounded_ast(query: str, code: str) -> None:
    policy = SQLPolicy()
    raw = candidate(query)
    try:
        parsed = policy.parse(raw)
    except AccessRejection as error:
        assert error.code == code
        return
    with pytest.raises(AccessRejection) as error:
        policy.evaluate(parsed, raw, context())
    assert error.value.code == code


def test_sql_policy_requires_relevant_predicate_without_server_tenant_filter() -> None:
    policy = SQLPolicy()
    raw = candidate("SELECT id FROM public.orders")
    parsed = policy.parse(raw)
    with pytest.raises(AccessRejection) as error:
        policy.evaluate(parsed, raw, context(required=False))
    assert error.value.code == "scope_violation"


def test_sql_generation_contract_matches_the_positive_ast_subset() -> None:
    access = context()
    contract = SQLPolicy().generation_contract(
        scope_config=access.scope_config,
        schema_catalog=access.schema_catalog,
    )

    assert contract["dialect"] == "postgres"
    assert contract["allowed_statement_modes"] == ["SELECT", "EXPLAIN SELECT"]
    assert "Union" not in contract["allowed_ast_nodes"]
    assert "UNION|INTERSECT|EXCEPT" in contract["forbidden_constructs"]
    assert "schema_catalog" not in contract


def test_sql_policy_uses_snapshot_mysql_dialect() -> None:
    policy = SQLPolicy()
    raw = candidate("SELECT `id`, `status` FROM `public`.`orders` WHERE `status` = 'failed'")
    parsed = policy.parse(raw)
    evaluated = policy.evaluate(parsed, raw, context(dialect="mysql"))
    assert evaluated.effective_action["adapter_kind"] == "mysql_sql"
    assert "`public`.`orders`" in evaluated.effective_action["query"]
