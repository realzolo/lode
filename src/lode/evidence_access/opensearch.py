"""OpenSearch-specific Query DSL security profile."""

from __future__ import annotations

from lode.evidence_access.search_policy import SearchPolicyProfile, StructuredSearchPolicy

OPENSEARCH_PROFILE = SearchPolicyProfile(
    language="opensearch_query_dsl",
    adapter_kind="opensearch",
    parser_name="opensearch-structured-json",
    parser_version="opensearch-2-3-safe-subset.1",
    policy_version="lode-opensearch-policy.1",
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


class OpenSearchQueryPolicy(StructuredSearchPolicy):
    def __init__(self) -> None:
        super().__init__(OPENSEARCH_PROFILE)
