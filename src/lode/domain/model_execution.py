"""Pure contracts for model routing, context assembly, and conclusion authority."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from lode.domain.errors import DomainValidationError
from lode.domain.types import ExecutionClass, ModelDataClass, ModelRole

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SourceRevisionOrigin = Literal["alert_revision", "bound_branch_head", "runtime_observed"]
SourceAuthorityStatus = Literal["authoritative", "corroborated", "contradicted", "unavailable"]
SourceCompatibilityStatus = Literal["not_checked", "compatible", "incompatible"]
ConfigurationStatus = Literal["unknown", "corroborated", "contradicted"]
MANDATORY_PINNED_EVIDENCE_KINDS = frozenset({"incident_input"})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, tuple | list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(child) for child in value)
    return value


def _required(value: str, name: str, maximum: int = 2_000) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise DomainValidationError("invalid_text", f"{name} must be trimmed and bounded")


def _refs(values: tuple[int, ...], name: str, *, allow_empty: bool = True) -> None:
    if not allow_empty and not values:
        raise DomainValidationError("empty_collection", f"{name} must not be empty")
    if len(values) != len(set(values)) or any(value < 1 for value in values):
        raise DomainValidationError("invalid_reference", f"{name} must contain unique positive IDs")


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    binding_snapshot_id: int
    workspace_model_binding_id: int
    provider_account_model_id: int
    provider_account_id: int
    provider_account_revision: int
    provider_account_model_revision: int
    provider_model_id: str
    execution_classes: tuple[ExecutionClass, ...]
    allowed_roles: tuple[ModelRole, ...]
    allowed_data_classes: tuple[str, ...]
    tokenizer_id: str
    context_window_tokens: int
    max_output_tokens: int
    provider_safety_margin_tokens: int
    max_cost_per_call: float
    max_context_utilization: float
    priority: int
    health_status: Literal["healthy", "unavailable"]
    predicted_cost: float = 0.0
    quality_score: float = 1.0
    provider_account_id_for_separation: int | None = None
    max_calls: int = 1
    used_calls: int = 0

    def __post_init__(self) -> None:
        numeric = (
            self.binding_snapshot_id,
            self.workspace_model_binding_id,
            self.provider_account_model_id,
            self.provider_account_id,
            self.provider_account_revision,
            self.provider_account_model_revision,
            self.context_window_tokens,
            self.max_output_tokens,
            self.provider_safety_margin_tokens,
        )
        if min(numeric) < 1 or self.priority < 0:
            raise DomainValidationError("invalid_reference", "model candidate limits are invalid")
        if not self.execution_classes or not self.allowed_roles or not self.allowed_data_classes:
            raise DomainValidationError(
                "empty_collection", "model candidate capabilities are required"
            )
        if len(self.execution_classes) != len(set(self.execution_classes)):
            raise DomainValidationError("duplicate_value", "execution classes must be unique")
        if not 0 < self.max_context_utilization < 1:
            raise DomainValidationError("invalid_model_limit", "context utilization is invalid")
        if self.max_calls < 1 or self.used_calls < 0:
            raise DomainValidationError("invalid_model_limit", "model call limit is invalid")
        if min(self.max_cost_per_call, self.predicted_cost, self.quality_score) < 0:
            raise DomainValidationError("invalid_model_limit", "model cost or quality is invalid")
        _required(self.tokenizer_id, "tokenizer_id", 200)
        _required(self.provider_model_id, "provider_model_id", 200)


@dataclass(frozen=True, slots=True)
class ModelTask:
    role: ModelRole
    required_context_tokens: int
    reserved_output_tokens: int
    provider_safety_margin_tokens: int
    data_class: str
    component_count: int = 1
    repository_count: int = 1
    contradiction_count: int = 0
    causal_depth: int = 1
    conclusion_risk: Literal["low", "medium", "high"] = "low"
    requested_execution_class: ExecutionClass | None = None
    prior_synthesizer_account_model_id: int | None = None
    prior_synthesizer_provider_id: int | None = None

    def __post_init__(self) -> None:
        if (
            min(
                self.required_context_tokens,
                self.reserved_output_tokens,
                self.provider_safety_margin_tokens,
                self.component_count,
                self.repository_count,
                self.causal_depth,
            )
            < 1
            or self.contradiction_count < 0
        ):
            raise DomainValidationError("invalid_model_task", "model task limits are invalid")
        _required(self.data_class, "data_class", 100)


@dataclass(frozen=True, slots=True)
class RouteExclusion:
    binding_snapshot_id: int
    code: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.binding_snapshot_id < 1:
            raise DomainValidationError("invalid_reference", "excluded binding must be positive")
        _required(self.code, "exclusion code", 100)
        object.__setattr__(self, "detail", _freeze(self.detail))


@dataclass(frozen=True, slots=True)
class SelectedModelRoute:
    candidate: ModelCandidate
    execution_class: ExecutionClass
    required_context_tokens: int
    allowed_input_tokens: int
    allowed_output_tokens: int
    selection_reason: str
    exclusions: tuple[RouteExclusion, ...]
    budget: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            min(
                self.required_context_tokens,
                self.allowed_input_tokens,
                self.allowed_output_tokens,
            )
            < 1
            or self.required_context_tokens > self.allowed_input_tokens
        ):
            raise DomainValidationError(
                "context_capacity_exceeded", "selected route cannot fit context"
            )
        _required(self.selection_reason, "selection_reason")
        object.__setattr__(self, "budget", _freeze(self.budget))


@dataclass(frozen=True, slots=True)
class ContextEvidence:
    artifact_id: int
    artifact_kind: str
    content: Mapping[str, Any]
    token_count: int
    relevance: float
    pinned: bool = False
    counter_evidence: bool = False
    data_class: str = "masked"

    def __post_init__(self) -> None:
        if self.artifact_id < 1 or self.token_count < 0 or self.relevance < 0:
            raise DomainValidationError("invalid_context_evidence", "context evidence is invalid")
        _required(self.artifact_kind, "artifact_kind", 100)
        _required(self.data_class, "data_class", 100)
        object.__setattr__(self, "content", _freeze(self.content))


def highest_model_data_class(evidence: Sequence[ContextEvidence]) -> str:
    priority = {
        ModelDataClass.MASKED.value: 0,
        ModelDataClass.SOURCE_CODE.value: 1,
        ModelDataClass.INTERNAL.value: 2,
        ModelDataClass.RESTRICTED.value: 3,
    }
    return max(
        (item.data_class for item in evidence),
        key=lambda value: priority.get(value, len(priority)),
        default=ModelDataClass.MASKED.value,
    )


def model_evidence_is_pinned(
    artifact_kind: str,
    configured_kinds: set[str] | frozenset[str],
) -> bool:
    return artifact_kind in MANDATORY_PINNED_EVIDENCE_KINDS or artifact_kind in configured_kinds


@dataclass(frozen=True, slots=True)
class AssembledContext:
    role: ModelRole
    state_packet: Mapping[str, Any]
    evidence: tuple[ContextEvidence, ...]
    summary_refs: tuple[int, ...]
    token_count: int
    reserved_output_tokens: int
    provider_safety_margin_tokens: int
    tokenizer_id: str
    context_hash: str

    def __post_init__(self) -> None:
        if min(self.reserved_output_tokens, self.provider_safety_margin_tokens) < 1:
            raise DomainValidationError(
                "invalid_context_bundle", "context reserves must be positive"
            )
        if self.token_count < 0 or not _SHA256.fullmatch(self.context_hash):
            raise DomainValidationError(
                "invalid_context_bundle", "context count or hash is invalid"
            )
        _refs(tuple(item.artifact_id for item in self.evidence), "evidence_refs")
        _refs(self.summary_refs, "summary_refs")
        _required(self.tokenizer_id, "tokenizer_id", 200)
        object.__setattr__(self, "state_packet", _freeze(self.state_packet))

    @property
    def evidence_refs(self) -> tuple[int, ...]:
        return tuple(item.artifact_id for item in self.evidence)

    @property
    def pinned_evidence_refs(self) -> tuple[int, ...]:
        return tuple(item.artifact_id for item in self.evidence if item.pinned)


@dataclass(frozen=True, slots=True)
class SourceAuthorityAssessment:
    repository_snapshot_id: int
    revision_origin: SourceRevisionOrigin
    requested_ref: str | None
    resolved_sha: str | None
    authority_status: SourceAuthorityStatus
    compatibility_status: SourceCompatibilityStatus
    runtime_evidence_refs: tuple[int, ...]
    mismatch_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.repository_snapshot_id < 1:
            raise DomainValidationError("invalid_reference", "repository snapshot must be positive")
        if (
            self.resolved_sha is not None
            and re.fullmatch(r"[0-9a-f]{40}", self.resolved_sha) is None
        ):
            raise DomainValidationError("invalid_revision", "resolved source SHA is invalid")
        _refs(self.runtime_evidence_refs, "runtime_evidence_refs")
        if len(self.mismatch_reasons) != len(set(self.mismatch_reasons)):
            raise DomainValidationError("duplicate_value", "mismatch reasons must be unique")

    @property
    def permits_confirmed_code(self) -> bool:
        return (
            self.authority_status
            in {
                "authoritative",
                "corroborated",
            }
            and self.compatibility_status != "incompatible"
        )


@dataclass(frozen=True, slots=True)
class ConfigurationAuthorityAssessment:
    scope: str
    declared_value: Any
    runtime_value: Any
    status: ConfigurationStatus
    evidence_refs: tuple[int, ...]

    def __post_init__(self) -> None:
        _required(self.scope, "configuration scope", 500)
        _refs(self.evidence_refs, "configuration evidence refs")
        if self.status != "unknown" and not self.evidence_refs:
            raise DomainValidationError(
                "missing_evidence", "runtime configuration status requires evidence"
            )
        object.__setattr__(self, "declared_value", _freeze(self.declared_value))
        object.__setattr__(self, "runtime_value", _freeze(self.runtime_value))
