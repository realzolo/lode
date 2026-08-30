from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lode.application.capabilities import CapabilityCatalogBuilder, catalog_for_model
from lode.application.decision_policy import DecisionPolicyEngine
from lode.domain.errors import DomainValidationError
from lode.domain.investigation import (
    CapabilityEntry,
    ConnectorCapabilitySnapshot,
    DecisionBudget,
    Hypothesis,
    InvestigationDecision,
    PlannedOperation,
)
from lode.domain.types import NativeLanguage


def budget(**overrides):
    values = {
        "remaining_operations": 8,
        "remaining_native_reads": 8,
        "remaining_output_bytes": 1_000_000,
        "remaining_cost": 10.0,
        "remaining_timeout_ms": 60_000,
    }
    values.update(overrides)
    return DecisionBudget(**values)


def capability(action_id: str = "native:7:sql", *, resource_key: str = "connector:3", **overrides):
    values = {
        "action_id": action_id,
        "operation_kind": "native_read",
        "evidence_types": ("database_row",),
        "evidence_anchors": ("incident.trace_id",),
        "resource_summary": {"tables": {"names": ["orders"], "count": 1}},
        "resource_key": resource_key,
        "server_cost": 2.0,
        "timeout_ms": 1_000,
        "result_limit": 10,
        "output_bytes": 10_000,
        "connector_snapshot_id": 7,
        "connector_id": 3,
        "native_language": NativeLanguage.SQL,
        "max_parallelism": 1,
    }
    values.update(overrides)
    return CapabilityEntry(**values)


def operation(action_id: str = "native:7:sql", **overrides):
    values = {
        "action_id": action_id,
        "purpose": "Check the current hypothesis",
        "expected_evidence": "One bounded database row",
        "evidence_anchors": ("incident.trace_id",),
        "supports_hypotheses": ("h1",),
        "refutes_hypotheses": (),
        "selection_reason": "This action distinguishes the likely mechanisms",
        "stop_condition": "Stop after one matching row",
        "estimated_cost": 0.0,
    }
    values.update(overrides)
    return PlannedOperation(**values)


def decision(*operations: PlannedOperation, hypotheses=None):
    return InvestigationDecision(
        "continue",
        tuple(hypotheses or (Hypothesis("h1", "The stored state is inconsistent"),)),
        tuple(operations),
        "Resolve the current evidence gap",
    )


def test_capability_catalog_is_minimal_stable_and_credential_free() -> None:
    snapshot = ConnectorCapabilitySnapshot(
        snapshot_id=7,
        connector_id=3,
        connector_kind="postgresql",
        connector_kind_version=1,
        allowed_languages=(NativeLanguage.SQL,),
        capabilities=("query",),
        schema_catalog={"tables": {"orders": {"secret": "must-not-be-exposed"}}},
        scope_config={"evidence_anchors": ["incident.trace_id"], "data_class": "masked"},
        execution_budget_policy={
            "max_timeout_ms": 2_000,
            "max_result_limit": 25,
            "max_output_bytes": 20_000,
            "max_total_output_bytes": 200_000,
            "max_native_reads": 8,
            "max_window_seconds": 7_200,
            "max_parallel_operations": 1,
            "estimated_cost": 1.5,
        },
        snapshot_hash="a" * 64,
        last_verified_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    entries = CapabilityCatalogBuilder().build(
        (snapshot,), budget=budget(), evidence_anchors=("incident.trace_id",)
    )
    model_value = catalog_for_model(entries)

    assert [value.action_id for value in entries] == ["native:7:sql"]
    assert "connector_id" not in model_value[0]
    assert model_value[0]["resource_summary"] == {"tables": {"names": ["orders"], "count": 1}}
    rendered = repr(model_value)
    assert "secret" not in rendered
    assert "config" not in rendered
    assert "credential" not in rendered


def test_policy_uses_server_cost_for_native_operation_intent() -> None:
    engine = DecisionPolicyEngine()
    value = decision(operation(estimated_cost=0.0))

    allowed = engine.evaluate(
        value,
        (capability(),),
        budget=budget(),
    )
    rejected_cost = engine.evaluate(
        value,
        (capability(),),
        budget=budget(remaining_cost=1.0),
    )

    assert allowed.outcome == "allow" and allowed.server_cost == 2.0
    assert rejected_cost.outcome == "reject"
    assert rejected_cost.policy_decisions[-1].code == "wave_budget_exceeded"


def test_policy_rejects_every_previously_attempted_operation() -> None:
    item = operation()

    result = DecisionPolicyEngine().evaluate(
        decision(item),
        (capability(),),
        budget=budget(),
        attempted_fingerprints={item.fingerprint},
    )

    assert result.outcome == "reject"
    assert result.policy_decisions[0].code == "duplicate_operation"


def test_policy_trims_dependency_duplicate_and_resource_conflict() -> None:
    first = operation()
    dependent = operation(
        action_id="native:8:sql",
        depends_on=(first.action_id,),
    )
    second = operation(action_id="native:9:sql")
    entries = (
        capability(),
        capability(
            "native:8:sql",
            resource_key="connector:4",
            connector_snapshot_id=8,
            connector_id=4,
        ),
        capability("native:9:sql", connector_snapshot_id=9),
    )

    result = DecisionPolicyEngine().evaluate(
        decision(first, dependent, second),
        entries,
        budget=budget(),
    )

    assert result.outcome == "trim"
    assert result.operations == (first,)
    assert {value.code for value in result.policy_decisions} >= {
        "dependent_wave_operation",
        "resource_conflict",
        "operation_allowed",
    }


def test_policy_requires_counter_evidence_before_confirmation_or_finish() -> None:
    hypothesis = Hypothesis(
        "h1", "The database state caused the incident", confirmation_requested=True
    )
    blocked = InvestigationDecision("finish", (hypothesis,), (), "Publish the result")
    gap = InvestigationDecision(
        "finish",
        (
            Hypothesis(
                "h1",
                "The database state caused the incident",
                confirmation_requested=True,
                counter_evidence_unavailable=True,
            ),
        ),
        (),
        "Publish the bounded result",
    )

    rejected = DecisionPolicyEngine().evaluate(blocked, (), budget=budget())
    accepted = DecisionPolicyEngine().evaluate(gap, (), budget=budget())

    assert rejected.outcome == "reject"
    assert rejected.policy_decisions[0].code == "counter_evidence_required"
    assert accepted.outcome == "allow"


def test_decision_contract_rejects_more_than_four_operations() -> None:
    with pytest.raises(DomainValidationError, match="one to four"):
        decision(*(operation(action_id=f"native:{index}:sql") for index in range(1, 6)))
