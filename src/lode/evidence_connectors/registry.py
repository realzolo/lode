"""Explicit production registry for every native evidence boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from lode.evidence_access.elasticsearch import ElasticsearchQueryPolicy
from lode.evidence_access.https import HTTPSPolicy
from lode.evidence_access.logql import LogQLPolicy
from lode.evidence_access.opensearch import OpenSearchQueryPolicy
from lode.evidence_access.registry import NativePolicyRegistry
from lode.evidence_access.sql import SQLPolicy
from lode.evidence_connectors.elasticsearch import ElasticsearchConnector
from lode.evidence_connectors.https import HTTPSConnector
from lode.evidence_connectors.loki import LokiConnector
from lode.evidence_connectors.mysql import MySQLConnector
from lode.evidence_connectors.opensearch import OpenSearchConnector
from lode.evidence_connectors.postgresql import PostgreSQLConnector
from lode.evidence_connectors.types import EvidenceConnectorContract

ConnectorFactory = Callable[
    [Mapping[str, Any], Mapping[str, str], Any | None],
    EvidenceConnectorContract,
]


_CONNECTORS: dict[str, ConnectorFactory] = {
    "loki": lambda config, secrets, runtime=None: LokiConnector(config, secrets, runtime),
    "elasticsearch": lambda config, secrets, runtime=None: ElasticsearchConnector(
        config, secrets, runtime
    ),
    "opensearch": lambda config, secrets, runtime=None: OpenSearchConnector(
        config, secrets, runtime
    ),
    "postgresql": lambda config, secrets, runtime=None: PostgreSQLConnector(
        config, secrets, runtime
    ),
    "mysql": lambda config, secrets, runtime=None: MySQLConnector(config, secrets, runtime),
    "https": lambda config, secrets, runtime=None: HTTPSConnector(config, secrets, runtime),
}


def build_native_policy_registry() -> NativePolicyRegistry:
    registry = NativePolicyRegistry()
    registry.register(LogQLPolicy())
    registry.register(ElasticsearchQueryPolicy())
    registry.register(OpenSearchQueryPolicy())
    registry.register(SQLPolicy())
    registry.register(HTTPSPolicy())
    return registry


def create_evidence_connector(
    kind: str,
    config: Mapping[str, Any],
    secrets: Mapping[str, str],
    runtime: Any | None = None,
) -> EvidenceConnectorContract:
    try:
        factory = _CONNECTORS[kind]
    except KeyError as exc:
        raise ValueError(f"evidence connector kind is not registered: {kind}") from exc
    return factory(config, secrets, runtime)


def native_connector_capabilities() -> Mapping[str, Mapping[str, Any]]:
    connector_types = {
        "loki": LokiConnector,
        "elasticsearch": ElasticsearchConnector,
        "opensearch": OpenSearchConnector,
        "postgresql": PostgreSQLConnector,
        "mysql": MySQLConnector,
        "https": HTTPSConnector,
    }
    return {
        kind: {
            "language": connector.language,
            "read_capabilities": connector.read_capabilities,
        }
        for kind, connector in connector_types.items()
    }
