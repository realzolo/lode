"""Server-owned budget intersection shared by every language policy."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from lode.domain.evidence_budget import ExecutionBudgetPolicy
from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.types import AccessContext, AccessRejection, EffectiveBudget


def intersect_budget(
    candidate: NativeReadCandidateInput,
    context: AccessContext,
) -> tuple[EffectiveBudget, dict[str, Any]]:
    policy = ExecutionBudgetPolicy.from_mapping(context.execution_budget_policy)
    if context.native_reads_used >= policy.max_native_reads:
        raise AccessRejection("budget_violation", "native read operation budget exhausted")

    if context.archived_bytes_used >= policy.max_total_output_bytes:
        raise AccessRejection("budget_violation", "archived evidence byte budget exhausted")
    output_bytes = min(
        policy.max_output_bytes,
        policy.max_total_output_bytes - context.archived_bytes_used,
    )

    window_start = None
    window_end = None
    if candidate.requested_window is not None:
        window_start = max(candidate.requested_window.start, context.investigation_window_start)
        window_end = min(candidate.requested_window.end, context.investigation_window_end)
        if window_end <= window_start:
            raise AccessRejection("scope_violation", "requested window does not intersect investigation")
        if window_end - window_start > timedelta(seconds=policy.max_window_seconds):
            window_end = window_start + timedelta(seconds=policy.max_window_seconds)

    budget = EffectiveBudget(
        window_start=window_start,
        window_end=window_end,
        result_limit=min(candidate.requested_limit, policy.max_result_limit),
        timeout_ms=min(candidate.requested_timeout_ms, policy.max_timeout_ms),
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
