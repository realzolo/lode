from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.elasticsearch import ElasticsearchQueryPolicy
from lode.evidence_access.logql import LogQLPolicy
from lode.evidence_access.opensearch import OpenSearchQueryPolicy
from lode.evidence_access.types import AccessContext, AccessRejection

SENTINEL = "__LODE_VALUE_REF_INCIDENT_TRACE__"


def access_context(language: str, *, scope: dict, catalog: dict) -> AccessContext:
    return AccessContext(
        investigation_id=1,
        operation_id=2,
        connector_snapshot_id=3,
        model_invocation_id=4,
        workspace_id=5,
        connector_id=6,
        snapshot_hash="a" * 64,
        allowed_languages=(language,),
        allowed_evidence_anchors=("incident.trace_id",),
        scope_config=scope,
        schema_catalog=catalog,
        execution_budget_policy={
            "max_native_reads": 8,
            "max_result_limit": 500,
            "max_timeout_ms": 10_000,
            "max_output_bytes": 1_000_000,
            "max_total_output_bytes": 2_000_000,
            "max_window_seconds": 1_800,
            "max_parallel_operations": 1,
            "estimated_cost": 0.0,
        },
        investigation_window_start=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
        investigation_window_end=datetime(2026, 8, 26, 10, 0, tzinfo=UTC),
    )


def candidate(language: str, payload: dict, *, bindings: dict[str, str] | None = None):
    return NativeReadCandidateInput.model_validate(
        {
            "schema_version": "native-read-candidate.v1",
            "action_id": "evidence.followup.1",
            "connector_id": 6,
            "language": language,
            "purpose": "Inspect bounded incident evidence",
            "expected_evidence": "Matching records",
            "evidence_anchors": ["incident.trace_id"],
            "payload": payload,
            "value_bindings": bindings or {},
            "requested_window": {
                "start": "2026-08-26T09:15:00Z",
                "end": "2026-08-26T09:45:00Z",
            },
            "requested_limit": 2_000,
            "requested_timeout_ms": 60_000,
        }
    )


def logql_context(**changes) -> AccessContext:
    scope = {
        "root_filter_dnf": [
            [
                {"label": "cluster", "operator": "equals", "values": ["prod"]},
                {"label": "namespace", "operator": "equals", "values": ["orders"]},
            ]
        ],
        "allowed_pipeline_stages": ["line_filter", "json", "label_filter"],
        "allow_metric_queries": True,
        "max_metric_range_seconds": 900,
        "max_metric_samples": 200,
        "max_grouping_depth": 1,
    }
    scope.update(changes)
    return access_context(
        "logql",
        scope=scope,
        catalog={
            "labels": ["cluster", "namespace", "app"],
            "fields": ["duration_ms", "level"],
        },
    )


def test_logql_generation_contract_publishes_effective_scope_only() -> None:
    contract = LogQLPolicy().generation_contract(
        scope_config={},
        schema_catalog={"labels": ["app"], "fields": []},
    )

    assert contract["allowed_pipeline_stages"] == ["line_filter"]
    assert contract["allowed_selector_labels"] == ["app"]
    assert "json" not in contract["allowed_pipeline_stages"]


def search_context(language: str, **changes) -> AccessContext:
    scope = {
        "allowed_indices": ["logs-orders"],
        "required_terms": {"tenant.id": "orders"},
        "timestamp_field": "@timestamp",
        "allowed_source_fields": ["@timestamp", "message", "trace.id", "duration_ms"],
        "default_source_fields": ["@timestamp", "message", "trace.id"],
        "max_query_clauses": 32,
        "max_query_depth": 8,
        "max_page_size": 100,
        "max_aggregation_depth": 2,
        "max_aggregation_buckets": 200,
        "max_terms_size": 20,
        "min_histogram_interval_seconds": 60,
    }
    scope.update(changes)
    return access_context(
        language,
        scope=scope,
        catalog={
            "indices": {
                "logs-orders": {
                    "fields": {
                        "@timestamp": {"type": "date", "searchable": True, "aggregatable": True},
                        "message": {"type": "text", "searchable": True, "aggregatable": False},
                        "trace.id": {
                            "type": "keyword",
                            "searchable": True,
                            "aggregatable": True,
                            "cardinality": 100,
                        },
                        "tenant.id": {
                            "type": "keyword",
                            "searchable": True,
                            "aggregatable": True,
                            "cardinality": 5,
                        },
                        "level": {
                            "type": "keyword",
                            "searchable": True,
                            "aggregatable": True,
                            "cardinality": 6,
                        },
                        "duration_ms": {
                            "type": "long",
                            "searchable": True,
                            "aggregatable": True,
                            "cardinality": 10_000,
                        },
                    }
                }
            }
        },
    )


def test_logql_full_parse_scope_injection_and_arbitrary_value_binding() -> None:
    policy = LogQLPolicy()
    item = candidate(
        "logql",
        {"query": f'{{app="worker"}} |= "{SENTINEL}" | json | duration_ms >= 2000'},
        bindings={SENTINEL: "incident.trace_id"},
    )
    parsed = policy.parse(item)
    evaluated = policy.evaluate(parsed, item, logql_context())
    raw = ' trace/值?x=1"quoted"\nnext '
    bound = policy.bind_values(parsed, evaluated, {SENTINEL: raw})

    assert 'cluster="prod"' in evaluated.effective_action["queries"][0]
    assert 'namespace="orders"' in evaluated.effective_action["queries"][0]
    assert evaluated.effective_action["limit"] == 500
    assert evaluated.effective_action["timeout_ms"] == 10_000
    assert raw not in evaluated.effective_action["queries"][0]
    assert json.dumps(raw, ensure_ascii=False) in bound.canonical_action["queries"][0]
    rebound = policy._parser.parse(bound.canonical_action["queries"][0])
    assert any(item.get("value") == raw for item in rebound.strings)
    assert bound.structural_hash == evaluated.effective_structural_hash


def test_initial_trace_discovery_replaces_model_app_with_complete_root_scope() -> None:
    policy = LogQLPolicy()
    item = candidate(
        "logql",
        {"query": f'{{app="payment-gateway"}} |= "{SENTINEL}"'},
        bindings={SENTINEL: "incident.trace_id"},
    )
    context = replace(
        logql_context(
            root_filter_dnf=[
                [
                    {
                        "label": "app",
                        "operator": "any_of",
                        "values": ["pornbox", "payment-gateway", "sonakit"],
                    }
                ]
            ]
        ),
        trace_discovery_required=True,
        trace_value_ref="incident.trace_id",
    )

    evaluated = policy.evaluate(policy.parse(item), item, context)
    query = evaluated.effective_action["queries"][0]

    assert 'app=~"^(?:pornbox|payment\\\\-gateway|sonakit)$"' in query
    assert evaluated.effective_action["trace_discovery"] is True
    assert evaluated.constraint_diff["root_filter"]["full_scope_discovery"] is True


@pytest.mark.parametrize(
    "query, code",
    [
        ('{app="worker"} trailing', "invalid_syntax"),
        ('{app=~"worker.*"}', "unsupported_node"),
        ('{app="worker"} | line_format "{{.message}}"', "unsupported_node"),
        ('{app="worker"} |= "prefix-__LODE_VALUE_REF_INCIDENT_TRACE__"', "invalid_syntax"),
    ],
)
def test_logql_rejects_incomplete_high_cost_and_non_value_sentinel(query: str, code: str) -> None:
    bindings = {SENTINEL: "incident.trace_id"} if SENTINEL in query else {}
    with pytest.raises(AccessRejection) as rejected:
        LogQLPolicy().parse(candidate("logql", {"query": query}, bindings=bindings))
    assert rejected.value.code == code


def test_logql_metric_queries_are_bounded_by_capability_and_range() -> None:
    policy = LogQLPolicy()
    item = candidate(
        "logql",
        {"query": 'sum by (app) (count_over_time({app="worker"}[5m]))'},
    )
    parsed = policy.parse(item)
    evaluated = policy.evaluate(parsed, item, logql_context())

    assert evaluated.effective_action["query_kind"] == "metric"
    assert evaluated.effective_action["step_seconds"] == 9

    with pytest.raises(AccessRejection) as disabled:
        policy.evaluate(parsed, item, logql_context(allow_metric_queries=False))
    assert disabled.value.code == "scope_violation"
    too_wide = candidate("logql", {"query": 'count_over_time({app="worker"}[30m])'})
    with pytest.raises(AccessRejection) as excessive:
        policy.evaluate(policy.parse(too_wide), too_wide, logql_context())
    assert excessive.value.code == "budget_violation"


@pytest.mark.parametrize(
    "policy, language",
    [
        (ElasticsearchQueryPolicy(), "elasticsearch_query_dsl"),
        (OpenSearchQueryPolicy(), "opensearch_query_dsl"),
    ],
)
def test_search_policy_enforces_scope_window_projection_and_value_binding(policy, language) -> None:
    item = candidate(
        language,
        {
            "path": "/logs-orders/_search",
            "body": {
                "query": {"term": {"trace.id": SENTINEL}},
                "size": 2_000,
                "_source": ["message", "trace.id", "secret"],
            },
        },
        bindings={SENTINEL: "incident.trace_id"},
    )
    parsed = policy.parse(item)
    evaluated = policy.evaluate(parsed, item, search_context(language))
    raw = '"},"script":{"source":"write"}'
    bound = policy.bind_values(parsed, evaluated, {SENTINEL: raw})
    body = evaluated.effective_action["body"]

    assert body["size"] == 500
    assert body["_source"] == ["message", "trace.id"]
    assert body["timeout"] == "10000ms"
    assert body["track_total_hits"] is False
    assert body["query"]["bool"]["filter"][0] == {"term": {"tenant.id": "orders"}}
    assert body["query"]["bool"]["filter"][1]["range"]["@timestamp"]["gte"].endswith("+00:00")
    assert bound.canonical_action["body"]["query"]["bool"]["filter"][2]["term"]["trace.id"] == raw
    assert "script" not in bound.canonical_action["body"]


@pytest.mark.parametrize(
    "body, code",
    [
        ({"query": {"script": {"script": "delete"}}}, "write_semantics"),
        ({"runtime_mappings": {"x": {"type": "keyword"}}}, "unsupported_node"),
        ({"query": {"wildcard": {"message": "*"}}}, "unsupported_node"),
        ({"query": {"term": {"secret": "x"}}}, "scope_violation"),
    ],
)
def test_elasticsearch_rejects_write_unknown_costly_and_out_of_scope_nodes(
    body: dict, code: str
) -> None:
    policy = ElasticsearchQueryPolicy()
    item = candidate(
        "elasticsearch_query_dsl",
        {"path": "/logs-orders/_search", "body": body},
    )
    with pytest.raises(AccessRejection) as rejected:
        parsed = policy.parse(item)
        policy.evaluate(parsed, item, search_context("elasticsearch_query_dsl"))
    assert rejected.value.code == code


def test_search_path_and_aggregation_costs_fail_closed() -> None:
    policy = ElasticsearchQueryPolicy()
    wildcard = candidate(
        "elasticsearch_query_dsl",
        {"path": "/logs-*/_search", "body": {"query": {"term": {"level": "error"}}}},
    )
    with pytest.raises(AccessRejection) as path_rejected:
        policy.parse(wildcard)
    assert path_rejected.value.code == "write_semantics"

    valid = candidate(
        "elasticsearch_query_dsl",
        {
            "path": "/logs-orders/_search",
            "body": {
                "query": {"term": {"level": "error"}},
                "aggs": {
                    "by_level": {
                        "terms": {"field": "level", "size": 100},
                        "aggs": {"latency": {"avg": {"field": "duration_ms"}}},
                    }
                },
            },
        },
    )
    evaluated = policy.evaluate(
        policy.parse(valid), valid, search_context("elasticsearch_query_dsl")
    )
    assert evaluated.effective_action["body"]["aggs"]["by_level"]["terms"]["size"] == 6

    excessive = candidate(
        "elasticsearch_query_dsl",
        {
            "path": "/logs-orders/_search",
            "body": {
                "query": {"term": {"level": "error"}},
                "aggs": {
                    "timeline": {"date_histogram": {"field": "@timestamp", "fixed_interval": "1s"}}
                },
            },
        },
    )
    with pytest.raises(AccessRejection) as buckets:
        policy.evaluate(
            policy.parse(excessive), excessive, search_context("elasticsearch_query_dsl")
        )
    assert buckets.value.code == "budget_violation"


def test_elasticsearch_and_opensearch_security_metadata_are_independent() -> None:
    elasticsearch = ElasticsearchQueryPolicy()
    opensearch = OpenSearchQueryPolicy()

    assert elasticsearch.language != opensearch.language
    assert elasticsearch.parser_name != opensearch.parser_name
    assert elasticsearch.parser_version != opensearch.parser_version
    assert elasticsearch.policy_version != opensearch.policy_version
