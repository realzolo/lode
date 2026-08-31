"""Strict structured contracts for synthesis and independent verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from lode.structured_output import StrictResponseModel, parse_json_document


class _StrictModel(StrictResponseModel):
    pass


class CausalNodePayload(_StrictModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    node_type: Literal[
        "impact",
        "trigger",
        "root_cause",
        "contributing_factor",
        "propagation",
        "detection",
        "mitigation",
        "recovery",
        "counter_evidence",
        "evidence_gap",
    ]
    status: Literal["confirmed", "hypothesis", "refuted", "unknown"]
    statement: str
    evidence_refs: tuple[int, ...]
    entity_refs: tuple[int, ...]

    @model_validator(mode="after")
    def evidence_matches_status(self):
        if self.status in {"confirmed", "refuted"} and not self.evidence_refs:
            raise ValueError("confirmed and refuted causal nodes require evidence")
        return self


class CausalEdgePayload(_StrictModel):
    edge_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    source_node_id: str
    target_node_id: str
    status: Literal["confirmed", "hypothesis", "refuted"]
    relation: Literal[
        "triggers",
        "causes",
        "contributes_to",
        "propagates_to",
        "detected_by",
        "mitigated_by",
        "recovers",
        "contradicts",
    ]
    statement: str
    evidence_refs: tuple[int, ...] = Field(min_length=1)


class CausalGraphPayload(_StrictModel):
    nodes: tuple[CausalNodePayload, ...] = Field(min_length=1)
    edges: tuple[CausalEdgePayload, ...]
    root_node_ids: tuple[str, ...]

    @model_validator(mode="after")
    def graph_is_a_dag(self):
        node_ids = [node.node_id for node in self.nodes]
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(node_ids) != len(set(node_ids)) or len(edge_ids) != len(set(edge_ids)):
            raise ValueError("causal graph node and edge IDs must be unique")
        known = set(node_ids)
        if not set(self.root_node_ids) <= known:
            raise ValueError("causal graph root references are invalid")
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in known}
        for edge in self.edges:
            if edge.source_node_id not in known or edge.target_node_id not in known:
                raise ValueError("causal edge references an unknown node")
            if edge.source_node_id == edge.target_node_id:
                raise ValueError("causal graph cannot contain self edges")
            if edge.relation != "contradicts":
                adjacency[edge.source_node_id].append(edge.target_node_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("causal graph must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target in adjacency[node_id]:
                visit(target)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in known:
            visit(node_id)
        return self


class CodeFindingPayload(_StrictModel):
    status: Literal["confirmed", "hypothesis", "no_defect", "not_found"]
    source_artifact_id: int | None = Field(gt=0)
    source_assessment_id: int | None = Field(gt=0)
    repository_id: int | None = Field(gt=0)
    revision: str | None = Field(pattern=r"^[0-9a-f]{40}$")
    revision_origin: Literal["alert_revision", "bound_branch_head", "runtime_observed"] | None
    path: str | None
    symbol: str | None
    start_line: int | None = Field(gt=0)
    end_line: int | None = Field(gt=0)
    issue_type: str | None
    faulty_behavior: str
    why_wrong: str
    expected_behavior: str
    trigger_condition: str
    propagation: tuple[str, ...]
    incident_evidence_refs: tuple[int, ...]
    supporting_evidence_refs: tuple[int, ...]
    counter_evidence_refs: tuple[int, ...]
    missing_validation: tuple[str, ...]
    test_scenario: str

    @model_validator(mode="after")
    def require_source_anchor(self):
        anchors = (
            self.source_artifact_id,
            self.source_assessment_id,
            self.repository_id,
            self.revision,
            self.revision_origin,
            self.path,
            self.symbol,
            self.start_line,
            self.end_line,
        )
        if self.status in {"confirmed", "hypothesis"} and any(value is None for value in anchors):
            raise ValueError("a code finding requires a complete source anchor")
        if self.status in {"no_defect", "not_found"} and any(
            value is not None for value in anchors
        ):
            raise ValueError("no-defect and not-found findings cannot claim a source anchor")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("code finding line range is invalid")
        return self


class ParticipantPayload(_StrictModel):
    entity_ref: int = Field(gt=0)
    display_name: str
    identity_status: Literal["verified", "provisional", "ambiguous", "unknown"]
    evidence_refs: tuple[int, ...]


class TimelineItemPayload(_StrictModel):
    occurred_at: datetime
    event_ref: int = Field(gt=0)
    summary: str
    evidence_refs: tuple[int, ...]


class SourceAssessmentPayload(_StrictModel):
    repository_id: int = Field(gt=0)
    build_unit_id: int | None = Field(gt=0)
    component_id: int | None = Field(gt=0)
    revision: str | None = Field(pattern=r"^[0-9a-f]{40}$")
    revision_origin: Literal["alert_revision", "bound_branch_head", "runtime_observed"]
    authority_status: Literal["authoritative", "corroborated", "contradicted", "unavailable"]
    compatibility_status: Literal["not_checked", "compatible", "incompatible"]
    mismatch_reasons: tuple[str, ...]
    evidence_refs: tuple[int, ...]


class ConfigurationAssessmentPayload(_StrictModel):
    scope: str
    declared_value_json: str = Field(max_length=256 * 1024)
    runtime_value_json: str = Field(max_length=256 * 1024)
    effective_status: Literal["unknown", "corroborated", "contradicted"]
    evidence_refs: tuple[int, ...]

    @field_validator("declared_value_json", "runtime_value_json")
    @classmethod
    def values_are_bounded_json(cls, value: str) -> str:
        parse_json_document(value)
        return value

    @property
    def declared_value(self) -> Any:
        return parse_json_document(self.declared_value_json)

    @property
    def runtime_value(self) -> Any:
        return parse_json_document(self.runtime_value_json)


class EvidenceStatementPayload(_StrictModel):
    text: str
    evidence_refs: tuple[int, ...] = Field(min_length=1)


class EvidenceGapPayload(_StrictModel):
    description: str
    consequence: str
    required_evidence: str
    related_node_ids: tuple[str, ...]


class ActionRecommendationPayload(_StrictModel):
    action_type: Literal["mitigate", "remediate", "validate", "prevent"]
    priority: Literal["P0", "P1", "P2", "P3"]
    title: str
    rationale: str
    validation: str
    evidence_refs: tuple[int, ...] = Field(min_length=1)


class InvestigationReportPayload(_StrictModel):
    result_state: Literal["confirmed", "hypothesis", "insufficient", "unavailable"]
    headline: str
    executive_summary: str
    impact_scope: tuple[EvidenceStatementPayload, ...]
    causal_graph: CausalGraphPayload
    code_findings: tuple[CodeFindingPayload, ...]
    participants: tuple[ParticipantPayload, ...]
    timeline_summary: tuple[TimelineItemPayload, ...]
    source_assessments: tuple[SourceAssessmentPayload, ...]
    configuration_assessments: tuple[ConfigurationAssessmentPayload, ...]
    counter_evidence: tuple[EvidenceStatementPayload, ...]
    evidence_gaps: tuple[EvidenceGapPayload, ...]
    action_recommendations: tuple[ActionRecommendationPayload, ...]

    @model_validator(mode="after")
    def report_references_are_coherent(self):
        node_ids = {node.node_id for node in self.causal_graph.nodes}
        if any(not set(gap.related_node_ids) <= node_ids for gap in self.evidence_gaps):
            raise ValueError("evidence gap references an unknown causal node")
        return self


class FindingVerdictPayload(_StrictModel):
    finding_index: int = Field(ge=0)
    verdict: Literal["approved", "rejected"]
    reasons: tuple[str, ...]
    evidence_refs: tuple[int, ...]

    @model_validator(mode="after")
    def approved_findings_are_evidence_bound(self):
        if self.verdict == "approved" and not self.evidence_refs:
            raise ValueError("approved finding verdicts require evidence")
        return self


class CausalElementVerdictPayload(_StrictModel):
    element_id: str
    verdict: Literal["approved", "rejected"]
    reasons: tuple[str, ...]
    evidence_refs: tuple[int, ...]

    @model_validator(mode="after")
    def approved_elements_are_evidence_bound(self):
        if self.verdict == "approved" and not self.evidence_refs:
            raise ValueError("approved causal verdicts require evidence")
        return self


class VerificationPayload(_StrictModel):
    verdict: Literal["approved", "rejected"]
    node_verdicts: tuple[CausalElementVerdictPayload, ...]
    edge_verdicts: tuple[CausalElementVerdictPayload, ...]
    finding_verdicts: tuple[FindingVerdictPayload, ...]
    alternative_explanations_checked: tuple[str, ...]
    counter_evidence_refs: tuple[int, ...]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def verdict_targets_are_unique(self):
        node_ids = [item.element_id for item in self.node_verdicts]
        edge_ids = [item.element_id for item in self.edge_verdicts]
        finding_indexes = [item.finding_index for item in self.finding_verdicts]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node verdict targets must be unique")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("edge verdict targets must be unique")
        if len(finding_indexes) != len(set(finding_indexes)):
            raise ValueError("finding verdict targets must be unique")
        return self


def report_json_schema() -> dict[str, Any]:
    schema = InvestigationReportPayload.response_json_schema()
    schema["title"] = "investigation-report.v1"
    return schema


def verification_json_schema() -> dict[str, Any]:
    schema = VerificationPayload.response_json_schema()
    schema["title"] = "investigation-verification.v1"
    return schema
