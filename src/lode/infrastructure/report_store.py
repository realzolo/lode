"""Semantic validation and durable publication of terminal investigation reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.reporting import InvestigationReportPayload, VerificationPayload
from lode.application.source_authority import ConfigurationAuthorityEngine
from lode.db.models import (
    AIInvocation,
    EvidenceArtifact,
    EvidenceLink,
    IncidentActionProposal,
    Investigation,
    InvestigationCodeFinding,
    InvestigationModelPolicySnapshot,
    InvestigationReport,
    InvestigationRepositorySnapshot,
    SourceAssessment,
    SourceRevision,
)
from lode.domain.investigation import canonical_hash
from lode.domain.model_execution import SourceAuthorityAssessment


class ReportValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedReport:
    investigation_id: int
    result_state: str
    report_hash: str
    finding_ids: tuple[int, ...]
    downgrade_reasons: tuple[str, ...]


class PostgresReportStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.configuration = ConfigurationAuthorityEngine()

    async def publish_unavailable(
        self,
        *,
        investigation_id: int,
        reason: str,
        synthesizer_invocation_id: int | None = None,
    ) -> PublishedReport:
        investigation = await self.session.get(Investigation, investigation_id)
        if investigation is None:
            raise ReportValidationError("investigation_not_found")
        value = {
            "result_state": "unavailable",
            "headline": "Investigation analysis unavailable",
            "executive_summary": "The analysis model or structured protocol was unavailable.",
            "impact_scope": [],
            "causal_graph": {
                "nodes": [
                    {
                        "node_id": "analysis_unavailable",
                        "node_type": "evidence_gap",
                        "status": "unknown",
                        "statement": "No causal conclusion was produced.",
                        "evidence_refs": [],
                        "entity_refs": [],
                    }
                ],
                "edges": [],
                "root_node_ids": [],
            },
            "code_finding_refs": [],
            "participants": [],
            "timeline_summary": [],
            "source_assessments": [],
            "configuration_assessments": [],
            "counter_evidence": [],
            "evidence_gaps": [
                {
                    "description": reason,
                    "consequence": "No causal conclusion can be established.",
                    "required_evidence": "Restore an eligible model and create a child investigation.",
                    "related_node_ids": ["analysis_unavailable"],
                }
            ],
            "action_recommendations": [],
        }
        report_hash = canonical_hash(value)
        existing = await self.session.get(InvestigationReport, investigation_id)
        if existing is not None:
            return PublishedReport(
                investigation_id,
                existing.result_state,
                existing.report_hash,
                (),
                (),
            )
        self.session.add(
            InvestigationReport(
                investigation_id=investigation_id,
                result_state="unavailable",
                headline=value["headline"],
                executive_summary=value["executive_summary"],
                impact_scope=[],
                causal_graph=value["causal_graph"],
                code_finding_refs=[],
                participants=[],
                timeline_summary=[],
                source_assessments=[],
                configuration_assessments=[],
                counter_evidence=[],
                evidence_gaps=value["evidence_gaps"],
                action_recommendations=[],
                synthesizer_invocation_id=synthesizer_invocation_id,
                verifier_invocation_id=None,
                report_hash=report_hash,
            )
        )
        investigation.result_state = "unavailable"
        investigation.status = "completed"
        investigation.finished_at = datetime.now(UTC)
        return PublishedReport(investigation_id, "unavailable", report_hash, (), ())

    async def publish(
        self,
        *,
        investigation_id: int,
        synthesis: Mapping[str, Any],
        synthesizer_invocation_id: int,
        verification: Mapping[str, Any] | None,
        verifier_invocation_id: int | None,
    ) -> PublishedReport:
        try:
            payload = InvestigationReportPayload.model_validate(synthesis)
            verifier = (
                VerificationPayload.model_validate(verification)
                if verification is not None
                else None
            )
        except ValidationError as exc:
            raise ReportValidationError("invalid_structured_report") from exc
        investigation = await self.session.get(Investigation, investigation_id)
        if investigation is None:
            raise ReportValidationError("investigation_not_found")
        synthesizer = await self._invocation(
            synthesizer_invocation_id, investigation_id, "synthesizer"
        )
        verifier_row = (
            await self._invocation(
                verifier_invocation_id,
                investigation_id,
                "verifier",
                require_success=False,
            )
            if verifier_invocation_id is not None
            else None
        )
        if verifier is not None and verifier_row is None:
            raise ReportValidationError("verifier_payload_invocation_mismatch")
        if verifier is not None and verifier_row.status != "succeeded":
            raise ReportValidationError("verifier_payload_invocation_mismatch")

        artifact_ids = frozenset(
            (
                await self.session.execute(
                    select(EvidenceArtifact.id).where(
                        EvidenceArtifact.investigation_id == investigation_id
                    )
                )
            ).scalars()
        )
        referenced = _report_evidence_refs(payload)
        if not referenced.issubset(artifact_ids):
            raise ReportValidationError("report_evidence_ownership_failed")

        source_rows = tuple(
            (
                await self.session.execute(
                    select(SourceAssessment, SourceRevision, InvestigationRepositorySnapshot)
                    .join(
                        SourceRevision,
                        SourceRevision.id == SourceAssessment.source_revision_id,
                    )
                    .join(
                        InvestigationRepositorySnapshot,
                        InvestigationRepositorySnapshot.id == SourceRevision.repository_snapshot_id,
                    )
                    .where(SourceAssessment.investigation_id == investigation_id)
                    .order_by(SourceAssessment.id)
                )
            ).all()
        )
        source_by_id = {
            assessment.id: (revision, snapshot) for assessment, revision, snapshot in source_rows
        }
        configuration_assessments = tuple(
            self.configuration.assess(
                scope=item.scope,
                declared_value=item.declared_value,
                runtime_value=item.runtime_value,
                runtime_evidence_refs=item.evidence_refs,
            )
            for item in payload.configuration_assessments
        )

        verifier_status = await self._verifier_status(
            investigation_id=investigation_id,
            synthesizer=synthesizer,
            verifier=verifier,
            verifier_row=verifier_row,
            payload=payload,
            artifact_ids=artifact_ids,
        )
        finding_ids: list[int] = []
        finding_statuses: list[str] = []
        for index, finding in enumerate(payload.code_findings):
            status = finding.status
            if status in {"confirmed", "hypothesis"}:
                source = await self._validate_source_anchor(investigation_id, finding, source_by_id)
                if status == "confirmed" and (
                    not source.permits_confirmed_code
                    or verifier_status != "approved"
                    or not _finding_approved(verifier, index)
                ):
                    status = "hypothesis"
            row_payload = finding.model_dump(mode="json")
            row_payload["status"] = status
            finding_hash = canonical_hash(
                {"investigation_id": investigation_id, "finding": row_payload}
            )
            row = (
                await self.session.execute(
                    select(InvestigationCodeFinding).where(
                        InvestigationCodeFinding.investigation_id == investigation_id,
                        InvestigationCodeFinding.finding_hash == finding_hash,
                    )
                )
            ).scalar_one_or_none()
            created = row is None
            if row is None:
                row = InvestigationCodeFinding(
                    investigation_id=investigation_id,
                    status=status,
                    source_artifact_id=finding.source_artifact_id,
                    source_assessment_id=finding.source_assessment_id,
                    repository_id=finding.repository_id,
                    revision=finding.revision,
                    revision_origin=finding.revision_origin,
                    path=finding.path,
                    symbol=finding.symbol,
                    start_line=finding.start_line,
                    end_line=finding.end_line,
                    issue_type=finding.issue_type,
                    faulty_behavior=finding.faulty_behavior,
                    why_wrong=finding.why_wrong,
                    expected_behavior=finding.expected_behavior,
                    trigger_condition=finding.trigger_condition,
                    propagation=list(finding.propagation),
                    incident_evidence_refs=list(finding.incident_evidence_refs),
                    supporting_evidence_refs=list(finding.supporting_evidence_refs),
                    counter_evidence_refs=list(finding.counter_evidence_refs),
                    missing_validation=list(finding.missing_validation),
                    test_scenario=finding.test_scenario,
                    verifier_invocation_id=verifier_invocation_id,
                    finding_hash=finding_hash,
                )
                self.session.add(row)
                await self.session.flush()
            finding_ids.append(row.id)
            finding_statuses.append(status)
            if created:
                for artifact_id in _finding_evidence_refs(finding):
                    self.session.add(
                        EvidenceLink(
                            investigation_id=investigation_id,
                            source_type="code_finding",
                            source_id=row.id,
                            artifact_id=artifact_id,
                            relation="supports",
                        )
                    )

        report_value = payload.model_dump(
            mode="json",
            exclude={"code_findings", "source_assessments", "configuration_assessments"},
        )
        report_value["code_finding_refs"] = finding_ids
        source_assessment_values = [
            {
                "repository_id": snapshot.repository_id,
                "build_unit_id": assessment.build_unit_snapshot_id,
                "component_id": assessment.component_snapshot_id,
                "revision": revision.resolved_sha,
                "revision_origin": revision.revision_origin,
                "authority_status": assessment.authority_status,
                "compatibility_status": assessment.compatibility_status,
                "mismatch_reasons": list(assessment.mismatch_reasons),
                "evidence_refs": list(assessment.evidence_refs),
            }
            for assessment, revision, snapshot in source_rows
        ]
        report_value["source_assessments"] = source_assessment_values
        report_value["configuration_assessments"] = [
            {
                "scope": item.scope,
                "declared_value": item.declared_value,
                "runtime_value": item.runtime_value,
                "effective_status": item.status,
                "evidence_refs": list(item.evidence_refs),
            }
            for item in configuration_assessments
        ]
        downgrade_reasons: list[str] = []
        causal_graph = dict(report_value["causal_graph"])
        nodes = [dict(value) for value in causal_graph["nodes"]]
        edges = [dict(value) for value in causal_graph["edges"]]
        if verifier_status != "approved":
            for node in nodes:
                if node["status"] == "confirmed":
                    node["status"] = "hypothesis"
            for edge in edges:
                if edge["status"] == "confirmed":
                    edge["status"] = "hypothesis"
            if report_value["result_state"] == "confirmed":
                report_value["result_state"] = "hypothesis"
            if any(
                value.status == "confirmed" for value in payload.causal_graph.nodes
            ) or any(value.status == "confirmed" for value in payload.causal_graph.edges):
                downgrade_reasons.append("independent_causal_verification_required")
        causal_graph["nodes"] = nodes
        causal_graph["edges"] = edges
        report_value["causal_graph"] = causal_graph
        if any(
            status != original.status
            for status, original in zip(
                finding_statuses, payload.code_findings, strict=True
            )
        ):
            downgrade_reasons.append("source_or_finding_verification_failed")
        final_report = report_value
        report_hash = canonical_hash(final_report)
        existing = await self.session.get(InvestigationReport, investigation_id)
        if existing is not None:
            if existing.report_hash != report_hash:
                raise ReportValidationError("published_report_is_immutable")
            return PublishedReport(
                investigation_id,
                existing.result_state,
                existing.report_hash,
                tuple(finding_ids),
                tuple(downgrade_reasons),
            )
        report_row = InvestigationReport(
            investigation_id=investigation_id,
            result_state=str(final_report["result_state"]),
            headline=str(final_report["headline"]),
            executive_summary=str(final_report["executive_summary"]),
            impact_scope=list(final_report["impact_scope"]),
            causal_graph=dict(final_report["causal_graph"]),
            code_finding_refs=list(final_report["code_finding_refs"]),
            participants=list(final_report["participants"]),
            timeline_summary=list(final_report["timeline_summary"]),
            source_assessments=list(final_report["source_assessments"]),
            configuration_assessments=list(final_report["configuration_assessments"]),
            counter_evidence=list(final_report["counter_evidence"]),
            evidence_gaps=list(final_report["evidence_gaps"]),
            action_recommendations=list(final_report["action_recommendations"]),
            synthesizer_invocation_id=synthesizer_invocation_id,
            verifier_invocation_id=verifier_invocation_id,
            report_hash=report_hash,
        )
        self.session.add(report_row)
        for recommendation in payload.action_recommendations:
            recommendation_value = recommendation.model_dump(mode="json")
            self.session.add(
                IncidentActionProposal(
                    incident_id=investigation.incident_id,
                    investigation_id=investigation_id,
                    action_type=recommendation.action_type,
                    priority=recommendation.priority,
                    title=recommendation.title,
                    rationale=recommendation.rationale,
                    validation=recommendation.validation,
                    evidence_refs=list(recommendation.evidence_refs),
                    proposal_hash=canonical_hash(recommendation_value),
                )
            )
        for artifact_id in referenced:
            self.session.add(
                EvidenceLink(
                    investigation_id=investigation_id,
                    source_type="report",
                    source_id=investigation_id,
                    artifact_id=artifact_id,
                    relation="derived_from",
                )
            )
        investigation.result_state = str(final_report["result_state"])
        investigation.status = "completed"
        investigation.finished_at = datetime.now(UTC)
        return PublishedReport(
            investigation_id,
            str(final_report["result_state"]),
            report_hash,
            tuple(finding_ids),
            tuple(downgrade_reasons),
        )

    async def _invocation(
        self,
        invocation_id: int | None,
        investigation_id: int,
        role: str,
        *,
        require_success: bool = True,
    ) -> AIInvocation:
        if invocation_id is None:
            raise ReportValidationError(f"{role}_invocation_missing")
        row = await self.session.get(AIInvocation, invocation_id)
        if (
            row is None
            or row.investigation_id != investigation_id
            or row.role != role
            or (require_success and row.status != "succeeded")
        ):
            raise ReportValidationError(f"{role}_invocation_invalid")
        return row

    async def _validate_source_anchor(
        self,
        investigation_id: int,
        finding,
        source_by_id: Mapping[int, Sequence[Any]],
    ) -> SourceAuthorityAssessment:
        artifact = await self.session.get(EvidenceArtifact, finding.source_artifact_id)
        row = source_by_id.get(finding.source_assessment_id)
        if artifact is None or row is None:
            raise ReportValidationError("code_finding_source_anchor_missing")
        revision, snapshot = row
        provenance = artifact.provenance
        expected = {
            "repository_snapshot_id": snapshot.id,
            "repository_id": finding.repository_id,
            "revision_origin": finding.revision_origin,
            "revision": finding.revision,
            "path": finding.path,
            "symbol": finding.symbol,
            "start_line": finding.start_line,
            "end_line": finding.end_line,
        }
        if (
            artifact.investigation_id != investigation_id
            or artifact.artifact_kind != "source_file"
            or revision.investigation_id != investigation_id
            or revision.repository_snapshot_id != snapshot.id
            or revision.resolved_sha != finding.revision
            or revision.revision_origin != finding.revision_origin
            or any(provenance.get(key) != value for key, value in expected.items())
        ):
            raise ReportValidationError("code_finding_source_provenance_mismatch")
        assessment = await self.session.get(SourceAssessment, finding.source_assessment_id)
        assert assessment is not None
        return SourceAuthorityAssessment(
            repository_snapshot_id=snapshot.id,
            revision_origin=revision.revision_origin,
            requested_ref=revision.requested_ref,
            resolved_sha=revision.resolved_sha,
            authority_status=assessment.authority_status,
            compatibility_status=assessment.compatibility_status,
            runtime_evidence_refs=tuple(assessment.evidence_refs),
            mismatch_reasons=tuple(assessment.mismatch_reasons),
        )

    async def _verifier_status(
        self,
        *,
        investigation_id: int,
        synthesizer: AIInvocation,
        verifier: VerificationPayload | None,
        verifier_row: AIInvocation | None,
        payload: InvestigationReportPayload,
        artifact_ids: frozenset[int],
    ) -> str | None:
        if verifier is None or verifier_row is None or verifier.verdict != "approved":
            return None if verifier is None else "rejected"
        policy = await self.session.get(InvestigationModelPolicySnapshot, investigation_id)
        verifier_policy = policy.policy.get("verifier_policy", {}) if policy else {}
        if (
            verifier_policy.get("separate_account_model", False)
            and verifier_row.provider_account_model_id == synthesizer.provider_account_model_id
        ):
            return "rejected"
        verifier_refs = {
            ref
            for verdict in (
                *verifier.node_verdicts,
                *verifier.edge_verdicts,
                *verifier.finding_verdicts,
            )
            for ref in verdict.evidence_refs
        } | set(verifier.counter_evidence_refs)
        if not verifier_refs.issubset(artifact_ids):
            return "rejected"
        node_evidence = {
            node.node_id: frozenset(node.evidence_refs) for node in payload.causal_graph.nodes
        }
        edge_evidence = {
            edge.edge_id: frozenset(edge.evidence_refs) for edge in payload.causal_graph.edges
        }
        finding_evidence = {
            index: _finding_evidence_refs(finding)
            for index, finding in enumerate(payload.code_findings)
        }
        if {item.element_id for item in verifier.node_verdicts} != set(node_evidence):
            return "rejected"
        if {item.element_id for item in verifier.edge_verdicts} != set(edge_evidence):
            return "rejected"
        if {item.finding_index for item in verifier.finding_verdicts} != set(
            finding_evidence
        ):
            return "rejected"
        if any(
            item.verdict == "approved"
            and not (set(item.evidence_refs) & node_evidence[item.element_id])
            for item in verifier.node_verdicts
        ):
            return "rejected"
        if any(
            item.verdict == "approved"
            and not (set(item.evidence_refs) & edge_evidence[item.element_id])
            for item in verifier.edge_verdicts
        ):
            return "rejected"
        if any(
            item.verdict == "approved"
            and not (set(item.evidence_refs) & finding_evidence[item.finding_index])
            for item in verifier.finding_verdicts
        ):
            return "rejected"
        if (
            verifier_policy.get("separate_provider", False)
            and verifier_row.provider_account_id == synthesizer.provider_account_id
        ):
            return "rejected"
        confirmed_findings = {
            index
            for index, finding in enumerate(payload.code_findings)
            if finding.status == "confirmed"
        }
        approved_findings = {
            item.finding_index for item in verifier.finding_verdicts if item.verdict == "approved"
        }
        confirmed_nodes = {
            node.node_id for node in payload.causal_graph.nodes if node.status == "confirmed"
        }
        approved_nodes = {
            item.element_id for item in verifier.node_verdicts if item.verdict == "approved"
        }
        confirmed_edges = {
            edge.edge_id for edge in payload.causal_graph.edges if edge.status == "confirmed"
        }
        approved_edges = {
            item.element_id for item in verifier.edge_verdicts if item.verdict == "approved"
        }
        return (
            "approved"
            if confirmed_findings.issubset(approved_findings)
            and confirmed_nodes.issubset(approved_nodes)
            and confirmed_edges.issubset(approved_edges)
            else "rejected"
        )


def _finding_approved(verifier: VerificationPayload | None, index: int) -> bool:
    return verifier is not None and any(
        item.finding_index == index and item.verdict == "approved"
        for item in verifier.finding_verdicts
    )


def _finding_evidence_refs(finding) -> frozenset[int]:
    return frozenset(
        (
            *(finding.incident_evidence_refs),
            *(finding.supporting_evidence_refs),
            *(finding.counter_evidence_refs),
            *((finding.source_artifact_id,) if finding.source_artifact_id else ()),
        )
    )


def _report_evidence_refs(payload: InvestigationReportPayload) -> frozenset[int]:
    values: set[int] = set()
    for item in payload.impact_scope:
        values.update(item.evidence_refs)
    for node in payload.causal_graph.nodes:
        values.update(node.evidence_refs)
    for edge in payload.causal_graph.edges:
        values.update(edge.evidence_refs)
    for finding in payload.code_findings:
        values.update(_finding_evidence_refs(finding))
    for participant in payload.participants:
        values.update(participant.evidence_refs)
    for item in payload.timeline_summary:
        values.update(item.evidence_refs)
    for item in payload.configuration_assessments:
        values.update(item.evidence_refs)
    for item in payload.counter_evidence:
        values.update(item.evidence_refs)
    for recommendation in payload.action_recommendations:
        values.update(recommendation.evidence_refs)
    return frozenset(values)
