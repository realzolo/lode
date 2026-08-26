from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lode.domain.audit import (
    AuthorizedEvidenceRead,
    ContextBundleRevision,
    EvidenceAccessDecision,
    InvestigationSnapshot,
    ModelRoutingDecision,
    NativeReadCandidate,
)
from lode.domain.errors import DomainValidationError
from lode.domain.types import (
    AccessOutcome,
    AccessRejectionCode,
    ExecutionClass,
    ModelRole,
    NativeLanguage,
)


HASH = "a" * 64


def test_snapshot_freezes_models_even_when_other_capabilities_are_absent() -> None:
    snapshot = InvestigationSnapshot(
        investigation_id=1,
        workspace_id=2,
        workspace_revision=3,
        model_policy_revision_id=4,
        model_binding_revision_ids=(5,),
        repository_binding_revision_ids=(),
        connector_scope_revision_ids=(),
        descriptor_revision_ids=(),
        resource_graph_revision_id=None,
        created_at=datetime.now(UTC),
    )
    assert snapshot.repository_binding_revision_ids == ()


def test_snapshot_requires_at_least_one_model_binding() -> None:
    with pytest.raises(DomainValidationError) as exc:
        InvestigationSnapshot(
            investigation_id=1,
            workspace_id=2,
            workspace_revision=3,
            model_policy_revision_id=4,
            model_binding_revision_ids=(),
            repository_binding_revision_ids=(),
            connector_scope_revision_ids=(),
            descriptor_revision_ids=(),
            resource_graph_revision_id=None,
            created_at=datetime.now(UTC),
        )
    assert exc.value.code == "empty_collection"


def test_model_route_cannot_select_a_model_that_cannot_fit_context() -> None:
    with pytest.raises(DomainValidationError) as exc:
        ModelRoutingDecision(
            investigation_id=1,
            role=ModelRole.PLANNER,
            workspace_model_binding_revision_id=2,
            model_deployment_revision_id=3,
            execution_class=ExecutionClass.LATENCY_OPTIMIZED,
            required_context_tokens=9000,
            allowed_input_tokens=8000,
            allowed_output_tokens=2000,
            excluded_candidates=(),
            selection_reason="lowest eligible cost",
            budget={"max_cost": 1.0},
            decision_hash=HASH,
        )
    assert exc.value.code == "context_capacity_exceeded"


def test_context_bundle_pinned_refs_must_be_present_in_evidence() -> None:
    with pytest.raises(DomainValidationError) as exc:
        ContextBundleRevision(
            investigation_id=1,
            routing_decision_id=2,
            role=ModelRole.PLANNER,
            state_packet={},
            evidence_refs=(10,),
            summary_refs=(),
            pinned_evidence_refs=(11,),
            tokenizer_id="tokenizer-v1",
            token_count=100,
            reserved_output_tokens=1000,
            provider_safety_margin_tokens=500,
            context_hash=HASH,
        )
    assert exc.value.code == "invalid_pinned_evidence"


def _candidate() -> NativeReadCandidate:
    return NativeReadCandidate(
        investigation_id=1,
        operation_id=2,
        connector_snapshot_id=3,
        model_invocation_id=4,
        action_id="log.trace-discovery",
        language=NativeLanguage.LOGQL,
        purpose="find matching events",
        expected_evidence="runtime events",
        evidence_anchors=("incident.trace_id",),
        payload={"query": "{app=\"worker\"}"},
        value_bindings={"__LODE_VALUE_REF_INCIDENT_TRACE__": "incident.trace_id"},
        requested_budget={"limit": 100},
        candidate_hash=HASH,
    )


def test_native_candidate_is_untrusted_immutable_data() -> None:
    candidate = _candidate()
    assert candidate.language is NativeLanguage.LOGQL
    with pytest.raises(TypeError):
        candidate.payload["query"] = "changed"  # type: ignore[index]


def test_allowed_access_decision_requires_effective_action_and_budget() -> None:
    with pytest.raises(DomainValidationError) as exc:
        EvidenceAccessDecision(
            investigation_id=1,
            candidate_id=2,
            outcome=AccessOutcome.ALLOW,
            parser_version="logql-parser-v1",
            policy_version="logql-policy-v1",
            parse_tree_hash=HASH,
            snapshot_authorization_hash=HASH,
            effective_action=None,
            effective_budget=None,
            rejection_code=None,
            decision_log=(),
            decision_hash=HASH,
        )
    assert exc.value.code == "invalid_access_decision"


def test_rejected_access_decision_cannot_smuggle_an_effective_action() -> None:
    with pytest.raises(DomainValidationError) as exc:
        EvidenceAccessDecision(
            investigation_id=1,
            candidate_id=2,
            outcome=AccessOutcome.REJECT,
            parser_version="sql-parser-v1",
            policy_version="sql-policy-v1",
            parse_tree_hash=HASH,
            snapshot_authorization_hash=HASH,
            effective_action={"query": "DELETE FROM orders"},
            effective_budget=None,
            rejection_code=AccessRejectionCode.WRITE_SEMANTICS,
            decision_log=({"stage": "read-only-proof", "result": "reject"},),
            decision_hash=HASH,
        )
    assert exc.value.code == "invalid_access_decision"


def test_authorization_is_hash_bound_and_expiring() -> None:
    now = datetime.now(UTC)
    authorization = AuthorizedEvidenceRead(
        investigation_id=1,
        access_decision_id=2,
        candidate_hash=HASH,
        snapshot_hash=HASH,
        policy_hash=HASH,
        effective_action_hash=HASH,
        fingerprint=HASH,
        token_hash=HASH,
        issued_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    assert authorization.expires_at > authorization.issued_at


def test_authorization_rejects_non_positive_lifetime() -> None:
    now = datetime.now(UTC)
    with pytest.raises(DomainValidationError) as exc:
        AuthorizedEvidenceRead(
            investigation_id=1,
            access_decision_id=2,
            candidate_hash=HASH,
            snapshot_hash=HASH,
            policy_hash=HASH,
            effective_action_hash=HASH,
            fingerprint=HASH,
            token_hash=HASH,
            issued_at=now,
            expires_at=now,
        )
    assert exc.value.code == "invalid_authorization_expiry"
