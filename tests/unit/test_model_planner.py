from __future__ import annotations

import pytest

from lode.application.investigation import InvestigationState, PlannerUnavailable
from lode.application.model_planner import (
    ModelDecisionResult,
    StructuredInvestigationPlanner,
)
from lode.domain.investigation import DecisionBudget, Hypothesis
from lode.infrastructure.investigation_planner import AuditedInvestigationDecisionModel


def state() -> InvestigationState:
    return InvestigationState(
        investigation_id=1,
        wave_count=0,
        hypotheses=(Hypothesis("h1", "A runtime dependency timed out"),),
        evidence_refs=(1,),
        evidence_anchors=("incident.trace_id",),
        allowed_value_refs=frozenset({"incident.trace_id"}),
        attempted_fingerprints=frozenset(),
        budget=DecisionBudget(10, 8, 1_000_000, 10, 100_000),
        state_packet={},
    )


class FakeModel:
    def __init__(self, payload):
        self.payload = payload

    async def decide(self, _state, _catalog, _rejection):
        return ModelDecisionResult(invocation_id=7, payload=self.payload)


class MissingSnapshotSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args):
        return None


def missing_snapshot_session_factory():
    return MissingSnapshotSession()


def payload() -> dict:
    return {
        "decision": "continue",
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "mechanism": "A runtime dependency timed out",
                "supporting_evidence_refs": [1],
                "counter_evidence_refs": [],
                "evidence_gaps": ["Need the matching runtime event"],
                "confirmation_requested": False,
                "counter_evidence_unavailable": False,
            }
        ],
        "operations": [
            {
                "action_id": "native:1:logql",
                "purpose": "Find the matching runtime event",
                "expected_evidence": "A trace-bound event",
                "evidence_anchors": ["incident.trace_id"],
                "supports_hypotheses": ["h1"],
                "refutes_hypotheses": [],
                "selection_reason": "The trace is the strongest available anchor",
                "stop_condition": "Stop after the bounded trace window",
                "estimated_cost": 0.1,
                "depends_on": [],
            }
        ],
        "objective": "Resolve the first runtime evidence gap",
        "next_model_hint": None,
    }


async def test_strict_planner_attaches_server_invocation_id() -> None:
    decision = await StructuredInvestigationPlanner(FakeModel(payload())).decide(state(), ())

    assert decision.model_invocation_id == 7
    assert decision.operations[0].supports_hypotheses == ("h1",)


async def test_old_hypothesis_refs_field_is_rejected_without_compatibility() -> None:
    value = payload()
    operation = value["operations"][0]
    operation["hypothesis_refs"] = operation.pop("supports_hypotheses")

    with pytest.raises(PlannerUnavailable, match="invalid_structured_output"):
        await StructuredInvestigationPlanner(FakeModel(value)).decide(state(), ())


async def test_model_cannot_supply_its_own_invocation_id() -> None:
    value = payload()
    value["model_invocation_id"] = 999

    with pytest.raises(PlannerUnavailable, match="invalid_structured_output"):
        await StructuredInvestigationPlanner(FakeModel(value)).decide(state(), ())


async def test_planner_rejects_removed_native_candidate_field_without_compatibility() -> None:
    value = payload()
    value["operations"][0]["native_candidate"] = None

    with pytest.raises(PlannerUnavailable, match="invalid_structured_output"):
        await StructuredInvestigationPlanner(FakeModel(value)).decide(state(), ())


async def test_old_opaque_native_candidate_field_is_rejected_without_compatibility() -> None:
    value = payload()
    value["operations"][0]["native_candidate_json"] = "{}"

    with pytest.raises(PlannerUnavailable, match="invalid_structured_output"):
        await StructuredInvestigationPlanner(FakeModel(value)).decide(state(), ())


async def test_planner_rejects_empty_hypotheses_as_controlled_unavailability() -> None:
    value = payload()
    value["decision"] = "finish"
    value["hypotheses"] = []
    value["operations"] = []

    with pytest.raises(PlannerUnavailable, match="invalid_structured_output"):
        await StructuredInvestigationPlanner(FakeModel(value)).decide(state(), ())


async def test_missing_frozen_model_policy_is_an_expected_unavailable_result() -> None:
    model = AuditedInvestigationDecisionModel(
        missing_snapshot_session_factory,  # type: ignore[arg-type]
        runtime=None,  # type: ignore[arg-type]
    )

    with pytest.raises(PlannerUnavailable, match="model_capability_unavailable"):
        await model.decide(state(), (), ())
