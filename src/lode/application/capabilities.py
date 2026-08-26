"""Build the minimal capability catalog exposed to an investigation planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

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


def _positive_int(value: Any, default: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    )


def _nonnegative_float(value: Any, default: float) -> float:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
        else default
    )


def _resource_summary(catalog: Mapping[str, Any]) -> Mapping[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("indices", "tables", "endpoints", "working_sets", "resources"):
        value = catalog.get(key)
        if isinstance(value, Mapping):
            names = sorted(str(name) for name in value)[:100]
            summary[key] = {"names": names, "count": len(value)}
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
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
            return tuple()
        anchors = tuple(dict.fromkeys(str(value) for value in evidence_anchors if value))
        entries = list(static_capabilities)
        for snapshot in sorted(snapshots, key=lambda value: value.snapshot_id):
            if snapshot.health_status != "healthy":
                continue
            policy = snapshot.execution_budget_policy
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
                _positive_int(policy.get("max_timeout_ms"), 10_000),
                max(1, budget.remaining_timeout_ms),
            )
            result_limit = _positive_int(policy.get("max_result_limit"), 100)
            output_bytes = min(
                _positive_int(policy.get("max_output_bytes"), 1_000_000),
                max(1, budget.remaining_output_bytes),
            )
            max_parallelism = _positive_int(policy.get("max_parallel_operations"), 1)
            server_cost = _nonnegative_float(policy.get("estimated_cost"), 0.0)
            if server_cost > budget.remaining_cost:
                continue
            for language in sorted(snapshot.allowed_languages, key=lambda value: value.value):
                entries.append(
                    CapabilityEntry(
                        action_id=f"native:{snapshot.snapshot_id}:{language.value}",
                        operation_kind="native_read",
                        evidence_types=_EVIDENCE_TYPES[language],
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
