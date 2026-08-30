"""Strict structured-output boundary for the dynamic investigation planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lode.application.investigation import InvestigationState, PlannerUnavailable
from lode.domain.errors import DomainValidationError
from lode.domain.investigation import (
    Hypothesis,
    InvestigationDecision,
    PlannedOperation,
    PolicyDecision,
)
from lode.structured_output import StrictResponseModel


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


PositiveEvidenceRef = Annotated[int, Field(gt=0)]
BoundedText = Annotated[str, Field(min_length=1, max_length=2_000)]


def _require_trimmed(value: str, field_name: str) -> None:
    if value != value.strip():
        raise ValueError(f"{field_name} must be trimmed")


def _require_unique(values: tuple[Any, ...], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique values")


class HypothesisPayload(StrictResponseModel):
    hypothesis_id: str = Field(pattern=r"^h[1-9][0-9]{0,5}$")
    mechanism: BoundedText
    supporting_evidence_refs: tuple[PositiveEvidenceRef, ...]
    counter_evidence_refs: tuple[PositiveEvidenceRef, ...]
    evidence_gaps: tuple[BoundedText, ...]
    confirmation_requested: bool
    counter_evidence_unavailable: bool

    @model_validator(mode="after")
    def domain_invariants(self):
        _require_trimmed(self.mechanism, "mechanism")
        _require_unique(self.supporting_evidence_refs, "supporting_evidence_refs")
        _require_unique(self.counter_evidence_refs, "counter_evidence_refs")
        _require_unique(self.evidence_gaps, "evidence_gaps")
        for gap in self.evidence_gaps:
            _require_trimmed(gap, "evidence_gaps")
        return self


class OperationPayload(StrictResponseModel):
    action_id: str = Field(pattern=r"^[a-z0-9][a-z0-9:._-]{0,199}$")
    purpose: BoundedText
    expected_evidence: BoundedText
    evidence_anchors: tuple[BoundedText, ...] = Field(min_length=1)
    supports_hypotheses: tuple[str, ...]
    refutes_hypotheses: tuple[str, ...]
    selection_reason: BoundedText
    stop_condition: BoundedText
    estimated_cost: float = Field(ge=0)
    depends_on: tuple[str, ...]

    @model_validator(mode="after")
    def candidate_is_valid(self):
        for value, field_name in (
            (self.purpose, "purpose"),
            (self.expected_evidence, "expected_evidence"),
            (self.selection_reason, "selection_reason"),
            (self.stop_condition, "stop_condition"),
        ):
            _require_trimmed(value, field_name)
        _require_unique(self.evidence_anchors, "evidence_anchors")
        _require_unique(self.supports_hypotheses, "supports_hypotheses")
        _require_unique(self.refutes_hypotheses, "refutes_hypotheses")
        _require_unique(self.depends_on, "depends_on")
        if not self.supports_hypotheses and not self.refutes_hypotheses:
            raise ValueError("operation must support or refute a hypothesis")
        for anchor in self.evidence_anchors:
            _require_trimmed(anchor, "evidence_anchors")
        return self


class ModelHintPayload(StrictResponseModel):
    role: Literal["planner", "native_query", "synthesizer", "verifier", "context_compactor"]
    execution_class: Literal["latency_optimized", "reasoning_optimized"]
    required_context_tokens: int = Field(gt=0)
    reason: BoundedText

    @model_validator(mode="after")
    def reason_is_trimmed(self):
        _require_trimmed(self.reason, "reason")
        return self


class InvestigationDecisionPayload(StrictResponseModel):
    decision: Literal["continue", "finish"]
    hypotheses: tuple[HypothesisPayload, ...] = Field(min_length=1, max_length=20)
    operations: tuple[OperationPayload, ...] = Field(max_length=4)
    objective: BoundedText
    next_model_hint: ModelHintPayload | None

    @model_validator(mode="after")
    def decision_shape(self):
        _require_trimmed(self.objective, "objective")
        _require_unique(tuple(item.hypothesis_id for item in self.hypotheses), "hypothesis_ids")
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
                        depends_on=value.depends_on,
                    )
                    for value in payload.operations
                ),
                objective=payload.objective,
                next_model_hint=(
                    payload.next_model_hint.model_dump(mode="json")
                    if payload.next_model_hint is not None
                    else None
                ),
                model_invocation_id=result.invocation_id,
            )
        except (ValidationError, DomainValidationError) as exc:
            raise PlannerUnavailable("invalid_structured_output") from exc


def decision_json_schema() -> dict[str, Any]:
    return InvestigationDecisionPayload.response_json_schema()
