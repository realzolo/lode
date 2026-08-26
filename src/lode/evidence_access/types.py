"""Capability-free policy values used before durable authorization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

REJECTION_CODES = frozenset(
    {
        "invalid_syntax",
        "unsupported_node",
        "write_semantics",
        "scope_violation",
        "budget_violation",
        "egress_violation",
        "sandbox_violation",
        "preflight_failed",
    }
)
EXECUTION_FAILURE_CODES = frozenset(
    {
        "authentication_failed",
        "rate_limited",
        "provider_timeout",
        "provider_unavailable",
        "invalid_response",
        "partial_response",
        "cost_exceeded",
        "egress_violation",
        "sandbox_violation",
    }
)


class AccessRejection(ValueError):
    def __init__(self, code: str, reason: str, detail: Mapping[str, Any] | None = None) -> None:
        if code not in REJECTION_CODES:
            raise ValueError(f"unstable evidence rejection code: {code}")
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.detail = dict(detail or {})


class EvidenceExecutionFailure(RuntimeError):
    def __init__(self, code: str, reason: str, detail: Mapping[str, Any] | None = None) -> None:
        if code not in EXECUTION_FAILURE_CODES:
            raise ValueError(f"unstable evidence execution failure code: {code}")
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.detail = dict(detail or {})


@dataclass(frozen=True, slots=True)
class AccessContext:
    investigation_id: int
    operation_id: int
    connector_snapshot_id: int
    model_invocation_id: int
    workspace_id: int
    connector_id: int
    snapshot_hash: str
    allowed_languages: tuple[str, ...]
    allowed_evidence_anchors: tuple[str, ...]
    scope_config: Mapping[str, Any]
    schema_catalog: Mapping[str, Any]
    execution_budget_policy: Mapping[str, Any]
    investigation_window_start: datetime
    investigation_window_end: datetime
    native_reads_used: int = 0
    archived_bytes_used: int = 0


@dataclass(frozen=True, slots=True)
class EffectiveBudget:
    window_start: datetime | None
    window_end: datetime | None
    result_limit: int
    timeout_ms: int
    output_bytes: int


@dataclass(frozen=True, slots=True)
class ParsedNativeAction:
    language: str
    canonical_action: Mapping[str, Any]
    parse_tree_hash: str
    structural_hash: str
    value_slots: Mapping[str, tuple[str | int, ...]]
    parser_name: str
    parser_version: str


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    effective_action: Mapping[str, Any]
    effective_structural_hash: str
    validation_decisions: tuple[Mapping[str, Any], ...]
    constraint_diff: Mapping[str, Any]
    effective_budget: EffectiveBudget


@dataclass(frozen=True, slots=True)
class BoundNativeAction:
    language: str
    canonical_action: Mapping[str, Any]
    structural_hash: str
    parse_tree_hash: str


@dataclass(frozen=True, slots=True)
class AuthorizedReadResult:
    outcome: str
    candidate_id: int
    decision_id: int
    rejection_code: str | None = None
    rejection_detail: Mapping[str, Any] | None = None
    authorized_read_id: int | None = None
    token: str | None = None
    fingerprint: str | None = None
