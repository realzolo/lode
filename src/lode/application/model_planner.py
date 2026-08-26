"""Strict structured-output boundary for the dynamic investigation planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lode.application.investigation import InvestigationState, PlannerUnavailable
from lode.domain.investigation import (
    Hypothesis,
    InvestigationDecision,
    PlannedOperation,
    PolicyDecision,
)


class ModelDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: int = Field(gt=0)
    payload: Mapping[str, Any]


class DecisionModel(Protocol):
    async def decide(
        self,
        state: InvestigationState,
        catalog: Sequence[Mapping[str, Any]],
        rejection: Sequence[PolicyDecision],
    ) -> ModelDecisionResult: ...


class HypothesisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(pattern=r"^h[1-9][0-9]{0,5}$")
    mechanism: str = Field(min_length=1, max_length=2_000)
    supporting_evidence_refs: tuple[int, ...] = ()
    counter_evidence_refs: tuple[int, ...] = ()
    evidence_gaps: tuple[str, ...] = ()
    confirmation_requested: bool = False
    counter_evidence_unavailable: bool = False


class OperationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(pattern=r"^[a-z0-9][a-z0-9:._-]{0,199}$")
    purpose: str = Field(min_length=1, max_length=2_000)
    expected_evidence: str = Field(min_length=1, max_length=2_000)
    evidence_anchors: tuple[str, ...] = Field(min_length=1)
    supports_hypotheses: tuple[str, ...] = ()
    refutes_hypotheses: tuple[str, ...] = ()
    selection_reason: str = Field(min_length=1, max_length=2_000)
    stop_condition: str = Field(min_length=1, max_length=2_000)
    estimated_cost: float = Field(ge=0)
    native_candidate: Mapping[str, Any] | None = None
    depends_on: tuple[str, ...] = ()


class InvestigationDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["continue", "finish"]
    hypotheses: tuple[HypothesisPayload, ...]
    operations: tuple[OperationPayload, ...]
    objective: str
    next_model_hint: Mapping[str, Any] | None = None

    @model_validator(mode="after")
    def decision_shape(self):
        if self.decision == "continue" and not 1 <= len(self.operations) <= 4:
            raise ValueError("continue requires one to four operations")
        if self.decision == "finish" and self.operations:
            raise ValueError("finish cannot contain operations")
        return self


class StructuredInvestigationPlanner:
    def __init__(self, model: DecisionModel) -> None:
        self.model = model

    async def decide(
        self,
        state: InvestigationState,
        catalog: Sequence[Mapping[str, Any]],
        rejection: Sequence[PolicyDecision] = (),
    ) -> InvestigationDecision:
        result = await self.model.decide(state, catalog, rejection)
        try:
            payload = InvestigationDecisionPayload.model_validate(result.payload)
        except ValidationError as exc:
            raise PlannerUnavailable("invalid_structured_output") from exc
        return InvestigationDecision(
            decision=payload.decision,
            hypotheses=tuple(
                Hypothesis(
                    hypothesis_id=value.hypothesis_id,
                    mechanism=value.mechanism,
                    supporting_evidence_refs=value.supporting_evidence_refs,
                    counter_evidence_refs=value.counter_evidence_refs,
                    evidence_gaps=value.evidence_gaps,
                    confirmation_requested=value.confirmation_requested,
                    counter_evidence_unavailable=value.counter_evidence_unavailable,
                )
                for value in payload.hypotheses
            ),
            operations=tuple(
                PlannedOperation(
                    action_id=value.action_id,
                    purpose=value.purpose,
                    expected_evidence=value.expected_evidence,
                    evidence_anchors=value.evidence_anchors,
                    supports_hypotheses=value.supports_hypotheses,
                    refutes_hypotheses=value.refutes_hypotheses,
                    selection_reason=value.selection_reason,
                    stop_condition=value.stop_condition,
                    estimated_cost=value.estimated_cost,
                    native_candidate=value.native_candidate,
                    depends_on=value.depends_on,
                )
                for value in payload.operations
            ),
            objective=payload.objective,
            next_model_hint=payload.next_model_hint,
            model_invocation_id=result.invocation_id,
        )


def decision_json_schema() -> dict[str, Any]:
    return InvestigationDecisionPayload.model_json_schema()
