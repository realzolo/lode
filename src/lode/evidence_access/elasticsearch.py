"""Elasticsearch-specific Query DSL security profile."""

from __future__ import annotations

from lode.evidence_access.search_policy import SearchPolicyProfile, StructuredSearchPolicy

ELASTICSEARCH_PROFILE = SearchPolicyProfile(
    language="elasticsearch_query_dsl",
    adapter_kind="elasticsearch",
    parser_name="elasticsearch-structured-json",
    parser_version="elasticsearch-8-9-safe-subset.1",
    policy_version="lode-elasticsearch-policy.1",
    allowed_query_nodes=frozenset(
        {
            "bool",
            "term",
            "terms",
            "range",
            "exists",
            "match",
            "match_phrase",
            "match_all",
        }
    ),
    allowed_aggregations=frozenset(
        {
            "date_histogram",
            "terms",
            "min",
            "max",
            "avg",
            "sum",
            "value_count",
            "percentiles",
        }
    ),
)


class ElasticsearchQueryPolicy(StructuredSearchPolicy):
    def __init__(self) -> None:
        super().__init__(ELASTICSEARCH_PROFILE)
