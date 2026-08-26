"""Pure contracts for dynamic investigation decisions and evidence graphs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Mapping

from lode.domain.errors import DomainValidationError
from lode.domain.types import NativeLanguage, RelationKind

DecisionKind = Literal["continue", "finish"]
OperationKind = Literal[
    "model", "source_read", "native_read", "snapshot", "validation", "synthesis"
]
PolicyOutcome = Literal["allow", "trim", "reject"]
OperationStatus = Literal["succeeded", "rejected", "failed", "interrupted"]

_ACTION_ID = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,199}$")
_HYPOTHESIS_ID = re.compile(r"^h[1-9][0-9]{0,5}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _required(value: str, field_name: str, *, maximum: int = 2_000) -> str:
    if not value or value != value.strip() or len(value) > maximum:
        raise DomainValidationError(
            "invalid_text", f"{field_name} must be non-blank, trimmed, and bounded"
        )
    return value


def _unique(values: tuple[Any, ...], field_name: str, *, allow_empty: bool = False) -> None:
    if not allow_empty and not values:
        raise DomainValidationError("empty_collection", f"{field_name} must not be empty")
    if len(values) != len(set(values)):
        raise DomainValidationError("duplicate_value", f"{field_name} must contain unique values")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(child) for child in value)
    return value


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(child) for child in value]
    if isinstance(value, frozenset | set):
        return sorted(_jsonable(child) for child in value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ConnectorCapabilitySnapshot:
    snapshot_id: int
    connector_id: int
    connector_kind: str
    connector_kind_version: int
    allowed_languages: tuple[NativeLanguage, ...]
    capabilities: tuple[str, ...]
    schema_catalog: Mapping[str, Any]
    scope_config: Mapping[str, Any]
    execution_budget_policy: Mapping[str, Any]
    snapshot_hash: str
    health_status: Literal["healthy", "unavailable"] = "healthy"
    last_verified_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            min(
                self.snapshot_id,
                self.connector_id,
                self.connector_kind_version,
            )
            < 1
        ):
            raise DomainValidationError(
                "invalid_reference", "capability references must be positive"
            )
        _required(self.connector_kind, "connector_kind", maximum=100)
        _unique(self.allowed_languages, "allowed_languages")
        _unique(self.capabilities, "capabilities")
        if not _SHA256.fullmatch(self.snapshot_hash):
            raise DomainValidationError("invalid_hash", "snapshot_hash must be SHA-256")
        if self.last_verified_at is not None and (
            self.last_verified_at.tzinfo is None or self.last_verified_at.utcoffset() is None
        ):
            raise DomainValidationError(
                "invalid_timestamp", "last_verified_at must include timezone"
            )
        object.__setattr__(self, "schema_catalog", _freeze(self.schema_catalog))
        object.__setattr__(self, "scope_config", _freeze(self.scope_config))
        object.__setattr__(self, "execution_budget_policy", _freeze(self.execution_budget_policy))


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    action_id: str
    operation_kind: OperationKind
    evidence_types: tuple[str, ...]
    evidence_anchors: tuple[str, ...]
    resource_summary: Mapping[str, Any]
    resource_key: str
    server_cost: float
    timeout_ms: int
    result_limit: int
    output_bytes: int
    connector_snapshot_id: int | None = None
    connector_id: int | None = None
    native_language: NativeLanguage | None = None
    freshness: datetime | None = None
    data_class: str = "masked"
    max_parallelism: int = 1

    def __post_init__(self) -> None:
        if _ACTION_ID.fullmatch(self.action_id) is None:
            raise DomainValidationError("invalid_action_id", "action_id is not canonical")
        _unique(self.evidence_types, "evidence_types")
        _unique(self.evidence_anchors, "evidence_anchors")
        _required(self.resource_key, "resource_key", maximum=200)
        _required(self.data_class, "data_class", maximum=100)
        if (
            self.server_cost < 0
            or min(self.timeout_ms, self.result_limit, self.output_bytes, self.max_parallelism) < 1
        ):
            raise DomainValidationError(
                "invalid_capability_budget", "capability limits are invalid"
            )
        native_refs = (self.connector_snapshot_id, self.connector_id, self.native_language)
        if self.operation_kind == "native_read":
            if any(value is None for value in native_refs):
                raise DomainValidationError(
                    "invalid_capability", "native read capability needs connector references"
                )
        elif any(value is not None for value in native_refs):
            raise DomainValidationError(
                "invalid_capability", "non-native capability cannot expose connector references"
            )
        if self.freshness is not None and (
            self.freshness.tzinfo is None or self.freshness.utcoffset() is None
        ):
            raise DomainValidationError("invalid_timestamp", "freshness must include timezone")
        object.__setattr__(self, "resource_summary", _freeze(self.resource_summary))


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: str
    mechanism: str
    supporting_evidence_refs: tuple[int, ...] = field(default_factory=tuple)
    counter_evidence_refs: tuple[int, ...] = field(default_factory=tuple)
    evidence_gaps: tuple[str, ...] = field(default_factory=tuple)
    confirmation_requested: bool = False
    counter_evidence_unavailable: bool = False

    def __post_init__(self) -> None:
        if _HYPOTHESIS_ID.fullmatch(self.hypothesis_id) is None:
            raise DomainValidationError(
                "invalid_hypothesis_id", "hypothesis_id must use the h<number> form"
            )
        _required(self.mechanism, "mechanism")
        _unique(self.supporting_evidence_refs, "supporting_evidence_refs", allow_empty=True)
        _unique(self.counter_evidence_refs, "counter_evidence_refs", allow_empty=True)
        _unique(self.evidence_gaps, "evidence_gaps", allow_empty=True)
        if any(value < 1 for value in self.supporting_evidence_refs + self.counter_evidence_refs):
            raise DomainValidationError("invalid_reference", "evidence refs must be positive")


@dataclass(frozen=True, slots=True)
class PlannedOperation:
    action_id: str
    purpose: str
    expected_evidence: str
    evidence_anchors: tuple[str, ...]
    supports_hypotheses: tuple[str, ...]
    refutes_hypotheses: tuple[str, ...]
    selection_reason: str
    stop_condition: str
    estimated_cost: float
    native_candidate: Mapping[str, Any] | None = None
    depends_on: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if _ACTION_ID.fullmatch(self.action_id) is None:
            raise DomainValidationError("invalid_action_id", "operation action_id is not canonical")
        for value, name in (
            (self.purpose, "purpose"),
            (self.expected_evidence, "expected_evidence"),
            (self.selection_reason, "selection_reason"),
            (self.stop_condition, "stop_condition"),
        ):
            _required(value, name)
        _unique(self.evidence_anchors, "evidence_anchors")
        _unique(self.supports_hypotheses, "supports_hypotheses", allow_empty=True)
        _unique(self.refutes_hypotheses, "refutes_hypotheses", allow_empty=True)
        _unique(self.depends_on, "depends_on", allow_empty=True)
        if not self.supports_hypotheses and not self.refutes_hypotheses:
            raise DomainValidationError(
                "missing_hypothesis_reference",
                "operation must support or refute an existing hypothesis",
            )
        if self.estimated_cost < 0:
            raise DomainValidationError("invalid_estimate", "estimated_cost must be non-negative")
        object.__setattr__(self, "native_candidate", _freeze(self.native_candidate))

    @property
    def fingerprint(self) -> str:
        return canonical_hash(
            {
                "action_id": self.action_id,
                "evidence_anchors": self.evidence_anchors,
                "native_candidate": self.native_candidate,
            }
        )


@dataclass(frozen=True, slots=True)
class InvestigationDecision:
    decision: DecisionKind
    hypotheses: tuple[Hypothesis, ...]
    operations: tuple[PlannedOperation, ...]
    objective: str
    next_model_hint: Mapping[str, Any] | None = None
    model_invocation_id: int | None = None

    def __post_init__(self) -> None:
        _required(self.objective, "objective")
        _unique(tuple(item.hypothesis_id for item in self.hypotheses), "hypothesis_ids")
        if self.decision == "finish" and self.operations:
            raise DomainValidationError(
                "invalid_decision", "finish decision cannot contain operations"
            )
        if self.decision == "continue" and not 1 <= len(self.operations) <= 4:
            raise DomainValidationError(
                "invalid_decision", "continue decision requires one to four operations"
            )
        if self.model_invocation_id is not None and self.model_invocation_id < 1:
            raise DomainValidationError(
                "invalid_reference", "model_invocation_id must be positive"
            )
        object.__setattr__(self, "next_model_hint", _freeze(self.next_model_hint))

    @property
    def decision_hash(self) -> str:
        return canonical_hash(self)


@dataclass(frozen=True, slots=True)
class DecisionBudget:
    remaining_operations: int
    remaining_native_reads: int
    remaining_output_bytes: int
    remaining_cost: float
    remaining_timeout_ms: int

    def __post_init__(self) -> None:
        if (
            min(
                self.remaining_operations,
                self.remaining_native_reads,
                self.remaining_output_bytes,
                self.remaining_cost,
                self.remaining_timeout_ms,
            )
            < 0
        ):
            raise DomainValidationError("invalid_budget", "remaining budgets must be non-negative")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    code: str
    outcome: Literal["allow", "trim", "reject"]
    action_id: str | None
    detail: Mapping[str, Any]

    def __post_init__(self) -> None:
        _required(self.code, "policy code", maximum=100)
        object.__setattr__(self, "detail", _freeze(self.detail))


@dataclass(frozen=True, slots=True)
class EvaluatedDecision:
    candidate: InvestigationDecision
    outcome: PolicyOutcome
    operations: tuple[PlannedOperation, ...]
    policy_decisions: tuple[PolicyDecision, ...]
    server_cost: float
    native_read_count: int
    output_bytes: int

    @property
    def selected_operation_count(self) -> int:
        return len(self.operations)


@dataclass(frozen=True, slots=True)
class OperationResult:
    status: OperationStatus
    result_masked: Mapping[str, Any]
    evidence_refs: tuple[int, ...]
    metrics: Mapping[str, Any]
    failure_code: str | None = None
    failure_detail: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _unique(self.evidence_refs, "evidence_refs", allow_empty=True)
        if any(value < 1 for value in self.evidence_refs):
            raise DomainValidationError("invalid_reference", "evidence refs must be positive")
        if self.status == "succeeded" and self.failure_code is not None:
            raise DomainValidationError(
                "invalid_operation_result", "successful result cannot have a failure code"
            )
        if self.status != "succeeded" and not self.failure_code:
            raise DomainValidationError(
                "invalid_operation_result", "non-success result requires a failure code"
            )
        object.__setattr__(self, "result_masked", _freeze(self.result_masked))
        object.__setattr__(self, "metrics", _freeze(self.metrics))
        object.__setattr__(self, "failure_detail", _freeze(self.failure_detail))


@dataclass(frozen=True, slots=True)
class NormalizedLogEvent:
    occurred_at: datetime
    connector_snapshot_id: int
    provider_position: str
    raw_excerpt_masked: str
    attributes_masked: Mapping[str, Any]
    resource_attributes_masked: Mapping[str, Any]
    trace_match: Mapping[str, Any]
    component_candidates: tuple[Mapping[str, Any], ...]
    relation_hints: tuple[Mapping[str, Any], ...]
    revision_hints: tuple[Mapping[str, Any], ...]
    provider_metadata: Mapping[str, Any]
    evidence_artifact_id: int

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise DomainValidationError("invalid_timestamp", "occurred_at must include timezone")
        if min(self.connector_snapshot_id, self.evidence_artifact_id) < 1:
            raise DomainValidationError("invalid_reference", "event references must be positive")
        _required(self.provider_position, "provider_position", maximum=1_000)
        if len(self.raw_excerpt_masked) > 40_000:
            raise DomainValidationError("invalid_event", "raw excerpt exceeds the event budget")
        value_hash = self.trace_match.get("value_hash")
        location = self.trace_match.get("location")
        if not isinstance(value_hash, str) or _SHA256.fullmatch(value_hash) is None:
            raise DomainValidationError("invalid_trace_match", "trace value hash is invalid")
        if not isinstance(location, str) or not location:
            raise DomainValidationError("invalid_trace_match", "trace location is required")
        for key in self.provider_metadata:
            if "." not in str(key):
                raise DomainValidationError(
                    "invalid_provider_metadata", "provider metadata keys must be namespaced"
                )
        for field_name in (
            "attributes_masked",
            "resource_attributes_masked",
            "trace_match",
            "component_candidates",
            "relation_hints",
            "revision_hints",
            "provider_metadata",
        ):
            object.__setattr__(self, field_name, _freeze(getattr(self, field_name)))

    @property
    def timeline_key(self) -> tuple[datetime, int, str]:
        return (self.occurred_at, self.connector_snapshot_id, self.provider_position)


@dataclass(frozen=True, slots=True)
class RelationEvidence:
    kind: RelationKind
    source_stable_key: str
    target_stable_key: str
    evidence_refs: tuple[int, ...]
    basis: Mapping[str, Any]

    def __post_init__(self) -> None:
        _required(self.source_stable_key, "source_stable_key", maximum=200)
        _required(self.target_stable_key, "target_stable_key", maximum=200)
        if self.source_stable_key == self.target_stable_key:
            raise DomainValidationError("invalid_relation", "relation endpoints must differ")
        _unique(self.evidence_refs, "evidence_refs")
        if any(value < 1 for value in self.evidence_refs):
            raise DomainValidationError(
                "invalid_reference", "relation evidence refs must be positive"
            )
        object.__setattr__(self, "basis", _freeze(self.basis))
