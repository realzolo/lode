"""Strict structured contracts for synthesis and independent verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CausePayload(_StrictModel):
    status: Literal["confirmed", "hypothesis", "not_found"]
    mechanism: str
    causal_chain: tuple[str, ...]
    evidence_refs: tuple[int, ...]


class CodeDiagnosisPayload(_StrictModel):
    status: Literal["confirmed", "hypothesis", "no_defect", "not_found"]
    summary: str
    finding_indices: tuple[int, ...]


class CodeFindingPayload(_StrictModel):
    status: Literal["confirmed", "hypothesis", "no_defect", "not_found"]
    source_artifact_id: int | None = Field(default=None, gt=0)
    source_assessment_id: int | None = Field(default=None, gt=0)
    repository_id: int | None = Field(default=None, gt=0)
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    revision_role: (
        Literal["incident_source", "repository_search_candidate", "runtime_identified"] | None
    ) = None
    path: str | None = None
    symbol: str | None = None
    start_line: int | None = Field(default=None, gt=0)
    end_line: int | None = Field(default=None, gt=0)
    issue_type: str | None = None
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
            self.revision_role,
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
    build_unit_id: int | None = Field(default=None, gt=0)
    component_id: int | None = Field(default=None, gt=0)
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    revision_role: Literal["incident_source", "repository_search_candidate", "runtime_identified"]
    runtime_match_status: Literal[
        "exact", "unverified", "corroborated", "contradicted", "unresolved"
    ]
    mismatch_reasons: tuple[str, ...]
    evidence_refs: tuple[int, ...]


class ConfigurationAssessmentPayload(_StrictModel):
    scope: str
    declared_value: Any
    runtime_value: Any
    effective_status: Literal["unknown", "corroborated", "contradicted"]
    evidence_refs: tuple[int, ...]


class EvidenceStatementPayload(_StrictModel):
    text: str
    evidence_refs: tuple[int, ...] = Field(min_length=1)


class InvestigationReportPayload(_StrictModel):
    result_state: Literal["confirmed", "hypothesis", "insufficient", "unavailable"]
    headline: str
    summary: str
    incident_cause: CausePayload
    code_diagnosis: CodeDiagnosisPayload
    code_findings: tuple[CodeFindingPayload, ...]
    participants: tuple[ParticipantPayload, ...]
    timeline_summary: tuple[TimelineItemPayload, ...]
    source_assessments: tuple[SourceAssessmentPayload, ...]
    configuration_assessments: tuple[ConfigurationAssessmentPayload, ...]
    confirmed_facts: tuple[EvidenceStatementPayload, ...]
    counter_evidence: tuple[EvidenceStatementPayload, ...]
    evidence_gaps: tuple[str, ...]
    next_step: str

    @model_validator(mode="after")
    def finding_indices_exist(self):
        if len(set(self.code_diagnosis.finding_indices)) != len(
            self.code_diagnosis.finding_indices
        ) or any(
            index < 0 or index >= len(self.code_findings)
            for index in self.code_diagnosis.finding_indices
        ):
            raise ValueError("code diagnosis finding indices are invalid")
        return self


class FindingVerdictPayload(_StrictModel):
    finding_index: int = Field(ge=0)
    verdict: Literal["approved", "rejected"]
    reasons: tuple[str, ...]
    evidence_refs: tuple[int, ...]


class VerificationPayload(_StrictModel):
    verdict: Literal["approved", "rejected"]
    finding_verdicts: tuple[FindingVerdictPayload, ...]
    alternative_explanations_checked: tuple[str, ...]
    counter_evidence_refs: tuple[int, ...]
    reasons: tuple[str, ...]


def report_json_schema() -> dict[str, Any]:
    return InvestigationReportPayload.model_json_schema()


def verification_json_schema() -> dict[str, Any]:
    return VerificationPayload.model_json_schema()
