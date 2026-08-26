"""Server-owned budget intersection shared by every language policy."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.types import AccessContext, AccessRejection, EffectiveBudget


DEFAULT_MAX_LIMIT = {
    "logql": 1_000,
    "elasticsearch_query_dsl": 1_000,
    "opensearch_query_dsl": 1_000,
    "sql": 500,
    "https": 1_000,
    "command": 1_000,
}


def intersect_budget(
    candidate: NativeReadCandidateInput,
    context: AccessContext,
) -> tuple[EffectiveBudget, dict[str, Any]]:
    policy = context.execution_budget_policy
    max_operations = _positive_int(policy, "max_native_reads", 8)
    if context.native_reads_used >= max_operations:
        raise AccessRejection("budget_violation", "native read operation budget exhausted")

    max_limit = _positive_int(
        policy,
        "max_result_limit",
        DEFAULT_MAX_LIMIT[candidate.language],
    )
    max_timeout = _positive_int(policy, "max_timeout_ms", 30_000)
    max_output_bytes = _positive_int(policy, "max_output_bytes", 2 * 1024 * 1024)
    total_output_bytes = _positive_int(policy, "max_total_output_bytes", 20 * 1024 * 1024)
    if context.archived_bytes_used >= total_output_bytes:
        raise AccessRejection("budget_violation", "archived evidence byte budget exhausted")
    output_bytes = min(max_output_bytes, total_output_bytes - context.archived_bytes_used)

    window_start = None
    window_end = None
    if candidate.requested_window is not None:
        window_start = max(candidate.requested_window.start, context.investigation_window_start)
        window_end = min(candidate.requested_window.end, context.investigation_window_end)
        max_window_seconds = _positive_int(policy, "max_window_seconds", 7_200)
        if window_end <= window_start:
            raise AccessRejection("scope_violation", "requested window does not intersect investigation")
        if window_end - window_start > timedelta(seconds=max_window_seconds):
            window_end = window_start + timedelta(seconds=max_window_seconds)

    budget = EffectiveBudget(
        window_start=window_start,
        window_end=window_end,
        result_limit=min(candidate.requested_limit, max_limit),
        timeout_ms=min(candidate.requested_timeout_ms, max_timeout),
        output_bytes=output_bytes,
    )
    diff: dict[str, Any] = {}
    if budget.result_limit != candidate.requested_limit:
        diff["requested_limit"] = {"requested": candidate.requested_limit, "effective": budget.result_limit}
    if budget.timeout_ms != candidate.requested_timeout_ms:
        diff["requested_timeout_ms"] = {
            "requested": candidate.requested_timeout_ms,
            "effective": budget.timeout_ms,
        }
    if candidate.requested_window is not None and (
        window_start != candidate.requested_window.start or window_end != candidate.requested_window.end
    ):
        diff["requested_window"] = {
            "requested": candidate.requested_window.model_dump(mode="json"),
            "effective": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        }
    return budget, diff


def _positive_int(policy: Mapping[str, Any], key: str, default: int) -> int:
    value = policy.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AccessRejection(
            "budget_violation",
            "snapshot contains an invalid execution budget",
            {"field": key},
        )
    return value
