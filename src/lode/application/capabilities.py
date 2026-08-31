"""Build the minimal capability catalog exposed to an investigation planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from lode.domain.evidence_budget import ExecutionBudgetPolicy
from lode.domain.investigation import (
    CapabilityEntry,
    ConnectorCapabilitySnapshot,
    DecisionBudget,
)
from lode.domain.types import NativeLanguage

_EVIDENCE_TYPES: Mapping[NativeLanguage, tuple[str, ...]] = {
    NativeLanguage.LOGQL: ("log_event", "log_metric"),
    NativeLanguage.ELASTICSEARCH_QUERY_DSL: ("log_event", "search_aggregation"),
    NativeLanguage.OPENSEARCH_QUERY_DSL: ("log_event", "search_aggregation"),
    NativeLanguage.SQL: ("database_row", "query_plan"),
    NativeLanguage.HTTPS: ("http_document", "http_metadata"),
    NativeLanguage.COMMAND: ("file_match",),
}

_PROVIDER_EVIDENCE_TYPES: Mapping[str, tuple[str, ...]] = {
    "prometheus": ("metric_boundary",),
    "tempo": ("span", "entity_relation"),
    "jaeger": ("span", "entity_relation"),
    "kubernetes": ("resource_state", "configuration"),
    "github": ("pipeline", "deployment"),
    "gitlab": ("pipeline", "deployment"),
    "argocd": ("deployment", "resource_state", "configuration"),
}


def _resource_summary(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("indices", "tables", "endpoints", "working_sets", "resources"):
        value = catalog.get(key)
        if isinstance(value, Mapping) or (
            isinstance(value, Sequence) and not isinstance(value, str | bytes)
        ):
            names = sorted(str(name) for name in value)[:100]
            summary[key] = {"names": names, "count": len(value)}
    if not summary:
        summary["catalog_keys"] = sorted(str(key) for key in catalog)[:100]
    return summary


class CapabilityCatalogBuilder:
    """Derive stable, credential-free planner actions from frozen snapshots."""

    def build(
        self,
        snapshots: Sequence[ConnectorCapabilitySnapshot],
        *,
        budget: DecisionBudget,
        evidence_anchors: Sequence[str],
        static_capabilities: Sequence[CapabilityEntry] = (),
    ) -> tuple[CapabilityEntry, ...]:
        if budget.remaining_operations == 0:
            return ()
        anchors = tuple(dict.fromkeys(str(value) for value in evidence_anchors if value))
        entries = list(static_capabilities)
        for snapshot in sorted(snapshots, key=lambda value: value.snapshot_id):
            if snapshot.health_status != "healthy":
                continue
            policy = ExecutionBudgetPolicy.from_mapping(snapshot.execution_budget_policy)
            scope = snapshot.scope_config
            snapshot_anchors = tuple(
                dict.fromkeys(
                    str(value)
                    for value in scope.get("evidence_anchors", anchors)
                    if isinstance(value, str) and value
                )
            )
            if not snapshot_anchors:
                continue
            timeout_ms = min(
                policy.max_timeout_ms,
                max(1, budget.remaining_timeout_ms),
            )
            result_limit = policy.max_result_limit
            output_bytes = min(
                policy.max_output_bytes,
                max(1, budget.remaining_output_bytes),
            )
            max_parallelism = policy.max_parallel_operations
            server_cost = policy.estimated_cost
            if server_cost > budget.remaining_cost:
                continue
            for language in sorted(snapshot.allowed_languages, key=lambda value: value.value):
                entries.append(
                    CapabilityEntry(
                        action_id=f"native:{snapshot.snapshot_id}:{language.value}",
                        operation_kind="native_read",
                        evidence_types=_PROVIDER_EVIDENCE_TYPES.get(
                            snapshot.connector_kind, _EVIDENCE_TYPES[language]
                        ),
                        evidence_anchors=snapshot_anchors,
                        resource_summary=_resource_summary(snapshot.schema_catalog),
                        resource_key=f"connector:{snapshot.connector_id}",
                        server_cost=server_cost,
                        timeout_ms=timeout_ms,
                        result_limit=result_limit,
                        output_bytes=output_bytes,
                        connector_snapshot_id=snapshot.snapshot_id,
                        connector_id=snapshot.connector_id,
                        native_language=language,
                        freshness=snapshot.last_verified_at,
                        data_class=str(scope.get("data_class", "masked")),
                        max_parallelism=max_parallelism,
                    )
                )
        unique: dict[str, CapabilityEntry] = {}
        for entry in entries:
            if entry.action_id in unique:
                raise ValueError(f"duplicate server action ID: {entry.action_id}")
            unique[entry.action_id] = entry
        return tuple(unique[key] for key in sorted(unique))


def catalog_for_model(entries: Sequence[CapabilityEntry]) -> tuple[Mapping[str, Any], ...]:
    """Return the only capability fields permitted in an untrusted model context."""

    return tuple(
        {
            "action_id": entry.action_id,
            "operation_kind": entry.operation_kind,
            "evidence_types": list(entry.evidence_types),
            "evidence_anchors": list(entry.evidence_anchors),
            "resource_summary": _plain(entry.resource_summary),
            "health": "healthy",
            "freshness": entry.freshness.isoformat()
            if isinstance(entry.freshness, datetime)
            else None,
            "data_class": entry.data_class,
            "native_language": (
                entry.native_language.value if entry.native_language is not None else None
            ),
            "server_budget": {
                "timeout_ms": entry.timeout_ms,
                "result_limit": entry.result_limit,
                "output_bytes": entry.output_bytes,
                "estimated_cost": entry.server_cost,
            },
        }
        for entry in entries
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(child) for child in value]
    return value
