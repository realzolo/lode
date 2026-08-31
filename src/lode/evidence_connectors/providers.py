"""First-class typed, read-only HTTP provider connectors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lode.evidence_access.orchestrator import ExecutionPermit
from lode.evidence_connectors.https import HTTPSConnector
from lode.evidence_connectors.types import EvidenceResultEnvelope, ProviderExecutionError


class _TypedReadOnlyHTTPConnector(HTTPSConnector):
    permitted_endpoint_ids: frozenset[str] = frozenset()
    normalized_kind = "resource_state"
    read_capabilities = ("typed_read", "endpoint_catalog", "normalized_evidence")

    async def preflight(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        self._assert_typed_endpoint(action)
        return {
            "provider": self.kind,
            "endpoint_id": action["endpoint_id"],
            "safe_read": True,
            "typed_operation": True,
        }

    async def execute(self, permit: ExecutionPermit) -> Mapping[str, Any]:
        action = self._action(permit)
        self._assert_typed_endpoint(action)
        base_result = await super().execute(permit)
        result = dict(base_result)
        record = result.get("record")
        result["normalized_records"] = self._normalize(record, str(action["endpoint_id"]))
        result["provider"] = self.kind
        return EvidenceResultEnvelope(
            projected=result,
            sealed_raw=getattr(base_result, "sealed_raw", dict(base_result)),
        )

    def _assert_typed_endpoint(self, action: Mapping[str, Any]) -> None:
        if action.get("method") not in {"GET", "HEAD"}:
            raise PermissionError(f"{self.kind} connector is read-only")
        if action.get("endpoint_id") not in self.permitted_endpoint_ids:
            raise PermissionError(f"{self.kind} operation is outside the typed catalog")

    def _normalize(self, value: Any, endpoint_id: str) -> list[dict[str, Any]]:
        records = _bounded_records(value)
        return [
            {
                "evidence_type": self.normalized_kind,
                "provider": self.kind,
                "operation": endpoint_id,
                "provider_position": index,
                "attributes": record,
            }
            for index, record in enumerate(records)
        ]


class PrometheusConnector(_TypedReadOnlyHTTPConnector):
    kind = "prometheus"
    read_capabilities = ("bounded_metric_query", "endpoint_catalog", "normalized_evidence")
    permitted_endpoint_ids = frozenset(
        {"prometheus.query_range", "prometheus.series", "prometheus.metadata"}
    )
    normalized_kind = "metric_boundary"


class TempoConnector(_TypedReadOnlyHTTPConnector):
    kind = "tempo"
    read_capabilities = ("bounded_trace_query", "endpoint_catalog", "normalized_evidence")
    permitted_endpoint_ids = frozenset({"tempo.trace_search", "tempo.service_graph"})
    normalized_kind = "span"


class JaegerConnector(_TypedReadOnlyHTTPConnector):
    kind = "jaeger"
    read_capabilities = ("bounded_trace_query", "endpoint_catalog", "normalized_evidence")
    permitted_endpoint_ids = frozenset({"jaeger.traces", "jaeger.services"})
    normalized_kind = "span"


class KubernetesConnector(_TypedReadOnlyHTTPConnector):
    kind = "kubernetes"
    read_capabilities = ("bounded_resource_read", "endpoint_catalog", "normalized_evidence")
    permitted_endpoint_ids = frozenset(
        {
            "kubernetes.pods",
            "kubernetes.events",
            "kubernetes.deployments",
            "kubernetes.statefulsets",
        }
    )
    normalized_kind = "resource_state"

    def _assert_typed_endpoint(self, action: Mapping[str, Any]) -> None:
        super()._assert_typed_endpoint(action)
        path = str(action.get("path", ""))
        forbidden = ("/secrets", "/exec", "/attach", "/portforward", "/proxy")
        if any(value in path.lower() for value in forbidden):
            raise PermissionError("Kubernetes sensitive and interactive operations are disabled")
        if "watch" in action.get("query", {}):
            raise PermissionError("Kubernetes watch operations are disabled")


class GitHubConnector(_TypedReadOnlyHTTPConnector):
    kind = "github"
    read_capabilities = (
        "bounded_deployment_read",
        "bounded_pipeline_read",
        "endpoint_catalog",
        "normalized_evidence",
    )
    permitted_endpoint_ids = frozenset(
        {"github.commits", "github.deployments", "github.workflow_runs"}
    )
    normalized_kind = "pipeline"


class GitLabConnector(_TypedReadOnlyHTTPConnector):
    kind = "gitlab"
    read_capabilities = (
        "bounded_deployment_read",
        "bounded_pipeline_read",
        "endpoint_catalog",
        "normalized_evidence",
    )
    permitted_endpoint_ids = frozenset(
        {"gitlab.commits", "gitlab.deployments", "gitlab.pipelines"}
    )
    normalized_kind = "pipeline"


class ArgoCDConnector(_TypedReadOnlyHTTPConnector):
    kind = "argocd"
    read_capabilities = (
        "bounded_deployment_read",
        "endpoint_catalog",
        "normalized_evidence",
    )
    permitted_endpoint_ids = frozenset(
        {"argocd.applications", "argocd.application_resources"}
    )
    normalized_kind = "deployment"


def _bounded_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, Mapping):
        candidates = next(
            (
                child
                for key in ("data", "items", "results", "traces", "deployments", "pipelines")
                if isinstance((child := value.get(key)), list)
            ),
            [value],
        )
    else:
        candidates = [{"value": value}]
    output: list[dict[str, Any]] = []
    for candidate in candidates[:10_000]:
        if isinstance(candidate, Mapping):
            output.append(dict(candidate))
        else:
            output.append({"value": candidate})
    if not output:
        raise ProviderExecutionError("invalid_response", "provider returned no typed records")
    return output
