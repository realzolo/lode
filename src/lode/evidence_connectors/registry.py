"""Explicit production policy and connector registry for log evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from lode.evidence_access.elasticsearch import ElasticsearchQueryPolicy
from lode.evidence_access.logql import LogQLPolicy
from lode.evidence_access.opensearch import OpenSearchQueryPolicy
from lode.evidence_access.registry import NativePolicyRegistry
from lode.evidence_connectors.elasticsearch import ElasticsearchConnector
from lode.evidence_connectors.loki import LokiConnector
from lode.evidence_connectors.opensearch import OpenSearchConnector
from lode.evidence_connectors.types import EvidenceConnectorContract, ProviderHTTPTransport

ConnectorFactory = Callable[
    [Mapping[str, Any], Mapping[str, str], ProviderHTTPTransport | None],
    EvidenceConnectorContract,
]


_CONNECTORS: dict[str, ConnectorFactory] = {
    "loki": lambda config, secrets, transport=None: LokiConnector(config, secrets, transport),
    "elasticsearch": lambda config, secrets, transport=None: ElasticsearchConnector(
        config, secrets, transport
    ),
    "opensearch": lambda config, secrets, transport=None: OpenSearchConnector(
        config, secrets, transport
    ),
}


def build_log_policy_registry() -> NativePolicyRegistry:
    registry = NativePolicyRegistry()
    registry.register(LogQLPolicy())
    registry.register(ElasticsearchQueryPolicy())
    registry.register(OpenSearchQueryPolicy())
    return registry


def create_log_connector(
    kind: str,
    config: Mapping[str, Any],
    secrets: Mapping[str, str],
    transport: ProviderHTTPTransport | None = None,
) -> EvidenceConnectorContract:
    try:
        factory = _CONNECTORS[kind]
    except KeyError as exc:
        raise ValueError(f"log connector kind is not registered: {kind}") from exc
    return factory(config, secrets, transport)


def log_connector_capabilities() -> Mapping[str, Mapping[str, Any]]:
    return {
        "loki": {
            "language": LokiConnector.language,
            "read_capabilities": LokiConnector.read_capabilities,
        },
        "elasticsearch": {
            "language": ElasticsearchConnector.language,
            "read_capabilities": ElasticsearchConnector.read_capabilities,
        },
        "opensearch": {
            "language": OpenSearchConnector.language,
            "read_capabilities": OpenSearchConnector.read_capabilities,
        },
    }
