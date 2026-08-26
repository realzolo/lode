"""Immutable snapshot, model-routing, and native-read audit records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from lode.domain.errors import DomainValidationError
from lode.domain.models import _freeze, _required, _unique_nonempty
from lode.domain.types import (
    AccessOutcome,
    AccessRejectionCode,
    ExecutionClass,
    ModelRole,
    NativeLanguage,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _positive_tuple(values: tuple[int, ...], field_name: str, *, allow_empty: bool = False) -> None:
    if not allow_empty:
        _unique_nonempty(values, field_name)
    elif len(values) != len(set(values)):
        raise DomainValidationError("duplicate_value", f"{field_name} must contain unique values")
    if any(value < 1 for value in values):
        raise DomainValidationError("invalid_reference", f"{field_name} must contain positive IDs")


def _hash(value: str, field_name: str) -> None:
    if not _SHA256.fullmatch(value):
        raise DomainValidationError("invalid_hash", f"{field_name} must be SHA-256")


def _timezone(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError("invalid_timestamp", f"{field_name} must include timezone")


@dataclass(frozen=True, slots=True)
class InvestigationSnapshot:
    investigation_id: int
    workspace_id: int
    workspace_revision: int
    model_policy_revision_id: int
    model_binding_revision_ids: tuple[int, ...]
    repository_binding_revision_ids: tuple[int, ...]
    connector_scope_revision_ids: tuple[int, ...]
    descriptor_revision_ids: tuple[int, ...]
    resource_graph_revision_id: int | None
    created_at: datetime

    def __post_init__(self) -> None:
        if min(
            self.investigation_id, self.workspace_id, self.workspace_revision,
            self.model_policy_revision_id,
        ) < 1:
            raise DomainValidationError("invalid_reference", "snapshot roots must be positive")
        _positive_tuple(self.model_binding_revision_ids, "model_binding_revision_ids")
        _positive_tuple(
            self.repository_binding_revision_ids, "repository_binding_revision_ids", allow_empty=True
        )
        _positive_tuple(
            self.connector_scope_revision_ids, "connector_scope_revision_ids", allow_empty=True
        )
        _positive_tuple(self.descriptor_revision_ids, "descriptor_revision_ids", allow_empty=True)
        if self.resource_graph_revision_id is not None and self.resource_graph_revision_id < 1:
            raise DomainValidationError("invalid_reference", "resource graph revision must be positive")
        _timezone(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ModelRoutingDecision:
    investigation_id: int
    role: ModelRole
    workspace_model_binding_revision_id: int
    model_deployment_revision_id: int
    execution_class: ExecutionClass
    required_context_tokens: int
    allowed_input_tokens: int
    allowed_output_tokens: int
    excluded_candidates: tuple[tuple[int, str], ...]
    selection_reason: str
    budget: Mapping[str, Any]
    decision_hash: str

    def __post_init__(self) -> None:
        if min(
            self.investigation_id, self.workspace_model_binding_revision_id,
            self.model_deployment_revision_id, self.required_context_tokens,
            self.allowed_input_tokens, self.allowed_output_tokens,
        ) < 1:
            raise DomainValidationError("invalid_reference", "routing references and limits must be positive")
        if self.required_context_tokens > self.allowed_input_tokens:
            raise DomainValidationError("context_capacity_exceeded", "route cannot fit required context")
        _required(self.selection_reason, "selection_reason")
        _hash(self.decision_hash, "decision_hash")
        object.__setattr__(self, "budget", _freeze(self.budget))


@dataclass(frozen=True, slots=True)
class ContextBundleRevision:
    investigation_id: int
    routing_decision_id: int
    role: ModelRole
    state_packet: Mapping[str, Any]
    evidence_refs: tuple[int, ...]
    summary_refs: tuple[int, ...]
    pinned_evidence_refs: tuple[int, ...]
    tokenizer_id: str
    token_count: int
    reserved_output_tokens: int
    provider_safety_margin_tokens: int
    context_hash: str
    revision: int = 1

    def __post_init__(self) -> None:
        if min(
            self.investigation_id, self.routing_decision_id, self.reserved_output_tokens,
            self.provider_safety_margin_tokens, self.revision,
        ) < 1 or self.token_count < 0:
            raise DomainValidationError("invalid_context_bundle", "context bundle limits are invalid")
        _positive_tuple(self.evidence_refs, "evidence_refs", allow_empty=True)
        _positive_tuple(self.summary_refs, "summary_refs", allow_empty=True)
        _positive_tuple(self.pinned_evidence_refs, "pinned_evidence_refs", allow_empty=True)
        if not set(self.pinned_evidence_refs).issubset(self.evidence_refs):
            raise DomainValidationError("invalid_pinned_evidence", "pinned refs must be included evidence")
        _required(self.tokenizer_id, "tokenizer_id")
        _hash(self.context_hash, "context_hash")
        object.__setattr__(self, "state_packet", _freeze(self.state_packet))


@dataclass(frozen=True, slots=True)
class NativeReadCandidate:
    investigation_id: int
    operation_id: int
    connector_snapshot_id: int
    model_invocation_id: int
    action_id: str
    language: NativeLanguage
    purpose: str
    expected_evidence: str
    evidence_anchors: tuple[str, ...]
    payload: Mapping[str, Any]
    value_bindings: Mapping[str, str]
    requested_budget: Mapping[str, Any]
    candidate_hash: str

    def __post_init__(self) -> None:
        if min(
            self.investigation_id, self.operation_id, self.connector_snapshot_id,
            self.model_invocation_id,
        ) < 1:
            raise DomainValidationError("invalid_reference", "candidate references must be positive")
        _required(self.action_id, "action_id")
        _required(self.purpose, "purpose")
        _required(self.expected_evidence, "expected_evidence")
        _unique_nonempty(self.evidence_anchors, "evidence_anchors")
        _hash(self.candidate_hash, "candidate_hash")
        object.__setattr__(self, "payload", _freeze(self.payload))
        object.__setattr__(self, "value_bindings", _freeze(self.value_bindings))
        object.__setattr__(self, "requested_budget", _freeze(self.requested_budget))


@dataclass(frozen=True, slots=True)
class EvidenceAccessDecision:
    investigation_id: int
    candidate_id: int
    outcome: AccessOutcome
    parser_version: str
    policy_version: str
    parse_tree_hash: str
    snapshot_authorization_hash: str
    effective_action: Mapping[str, Any] | None
    effective_budget: Mapping[str, Any] | None
    rejection_code: AccessRejectionCode | None
    decision_log: tuple[Mapping[str, Any], ...]
    decision_hash: str

    def __post_init__(self) -> None:
        if min(self.investigation_id, self.candidate_id) < 1:
            raise DomainValidationError("invalid_reference", "access decision references must be positive")
        _required(self.parser_version, "parser_version")
        _required(self.policy_version, "policy_version")
        for value, name in (
            (self.parse_tree_hash, "parse_tree_hash"),
            (self.snapshot_authorization_hash, "snapshot_authorization_hash"),
            (self.decision_hash, "decision_hash"),
        ):
            _hash(value, name)
        if self.outcome is AccessOutcome.ALLOW:
            if self.rejection_code is not None or self.effective_action is None or self.effective_budget is None:
                raise DomainValidationError("invalid_access_decision", "allowed decision needs an action and budget")
        elif (
            self.rejection_code is None
            or self.effective_action is not None
            or self.effective_budget is not None
        ):
            raise DomainValidationError("invalid_access_decision", "rejected decision needs only a rejection code")
        object.__setattr__(self, "effective_action", _freeze(self.effective_action))
        object.__setattr__(self, "effective_budget", _freeze(self.effective_budget))
        object.__setattr__(self, "decision_log", _freeze(self.decision_log))


@dataclass(frozen=True, slots=True)
class AuthorizedEvidenceRead:
    investigation_id: int
    access_decision_id: int
    candidate_hash: str
    snapshot_hash: str
    policy_hash: str
    effective_action_hash: str
    fingerprint: str
    token_hash: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if min(self.investigation_id, self.access_decision_id) < 1:
            raise DomainValidationError("invalid_reference", "authorization references must be positive")
        for value, name in (
            (self.candidate_hash, "candidate_hash"), (self.snapshot_hash, "snapshot_hash"),
            (self.policy_hash, "policy_hash"), (self.effective_action_hash, "effective_action_hash"),
            (self.fingerprint, "fingerprint"), (self.token_hash, "token_hash"),
        ):
            _hash(value, name)
        _timezone(self.issued_at, "issued_at")
        _timezone(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise DomainValidationError("invalid_authorization_expiry", "authorization must expire later")


@dataclass(frozen=True, slots=True)
class EvidenceReadAttempt:
    investigation_id: int
    authorized_read_id: int
    attempt: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    result_artifact_refs: tuple[int, ...]
    metrics: Mapping[str, Any]
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if min(self.investigation_id, self.authorized_read_id, self.attempt) < 1:
            raise DomainValidationError("invalid_reference", "attempt references must be positive")
        _required(self.status, "status")
        _timezone(self.started_at, "started_at")
        if self.finished_at is not None:
            _timezone(self.finished_at, "finished_at")
            if self.finished_at < self.started_at:
                raise DomainValidationError("invalid_timestamp", "attempt cannot finish before start")
        _positive_tuple(self.result_artifact_refs, "result_artifact_refs", allow_empty=True)
        object.__setattr__(self, "metrics", _freeze(self.metrics))
