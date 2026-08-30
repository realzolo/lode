"""Semantic validation and durable publication of terminal investigation reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.conclusion_validation import ConclusionValidator
from lode.application.reporting import InvestigationReportPayload, VerificationPayload
from lode.application.source_authority import ConfigurationAuthorityEngine
from lode.db.models import (
    AIInvocation,
    EvidenceArtifact,
    EvidenceLink,
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
        self.conclusions = ConclusionValidator()
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
            "summary": "The analysis model or its structured protocol was unavailable.",
            "incident_cause": {
                "status": "not_found",
                "mechanism": "unavailable",
                "causal_chain": [],
                "evidence_refs": [],
            },
            "code_diagnosis": {
                "status": "not_found",
                "summary": "No code diagnosis was published.",
                "finding_refs": [],
            },
            "participants": [],
            "timeline_summary": [],
            "source_assessments": [],
            "configuration_assessments": [],
            "confirmed_facts": [],
            "counter_evidence": [],
            "evidence_gaps": [reason],
            "next_step": "Restore an eligible analysis model and retry the investigation.",
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
                summary=value["summary"],
                incident_cause=value["incident_cause"],
                code_diagnosis=value["code_diagnosis"],
                participants=[],
                timeline_summary=[],
                source_assessments=[],
                configuration_assessments=[],
                confirmed_facts=[],
                counter_evidence=[],
                evidence_gaps=[reason],
                next_step=value["next_step"],
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
        repository_snapshots = tuple(
            (
                await self.session.execute(
                    select(InvestigationRepositorySnapshot)
                    .where(InvestigationRepositorySnapshot.investigation_id == investigation_id)
                    .order_by(InvestigationRepositorySnapshot.id)
                )
            )
            .scalars()
            .all()
        )
        source_by_id = {
            assessment.id: (revision, snapshot) for assessment, revision, snapshot in source_rows
        }
        source_authority = tuple(
            SourceAuthorityAssessment(
                repository_snapshot_id=revision.repository_snapshot_id,
                revision_role=revision.revision_role,
                requested_ref=revision.requested_ref,
                resolved_sha=revision.resolved_sha,
                status=assessment.runtime_match_status,
                runtime_evidence_refs=tuple(assessment.evidence_refs),
                mismatch_reasons=tuple(assessment.mismatch_reasons),
            )
            for assessment, revision, _ in source_rows
        )
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
                    revision_role=finding.revision_role,
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

        report_value = payload.model_dump(mode="json", exclude={"code_findings"})
        selected_finding_ids = tuple(
            finding_ids[index] for index in payload.code_diagnosis.finding_indices
        )
        code_diagnosis = dict(report_value["code_diagnosis"])
        code_diagnosis.pop("finding_indices", None)
        code_diagnosis["finding_refs"] = list(selected_finding_ids)
        if code_diagnosis["status"] == "confirmed" and any(
            finding_statuses[index] != "confirmed"
            for index in payload.code_diagnosis.finding_indices
        ):
            code_diagnosis["status"] = "hypothesis"
        report_value["code_diagnosis"] = code_diagnosis
        source_assessment_values = [
            {
                "repository_id": snapshot.repository_id,
                "build_unit_id": assessment.build_unit_snapshot_id,
                "component_id": assessment.component_snapshot_id,
                "revision": revision.resolved_sha,
                "revision_role": revision.revision_role,
                "runtime_match_status": assessment.runtime_match_status,
                "mismatch_reasons": list(assessment.mismatch_reasons),
                "evidence_refs": list(assessment.evidence_refs),
            }
            for assessment, revision, snapshot in source_rows
        ]
        assessed_snapshot_ids = {revision.repository_snapshot_id for _, revision, _ in source_rows}
        source_assessment_values.extend(
            {
                "repository_id": snapshot.repository_id,
                "build_unit_id": None,
                "component_id": None,
                "revision": snapshot.frozen_candidate_sha,
                "revision_role": snapshot.frozen_revision_role,
                "runtime_match_status": snapshot.frozen_resolution_status,
                "mismatch_reasons": _snapshot_mismatch_reasons(snapshot),
                "evidence_refs": [],
            }
            for snapshot in repository_snapshots
            if snapshot.id not in assessed_snapshot_ids
        )
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
        validated = self.conclusions.validate(
            report_value,
            source_assessments=source_authority,
            configuration_assessments=configuration_assessments,
            verifier_status=verifier_status,
        )
        final_report = dict(validated.report)
        gaps = list(final_report.get("evidence_gaps", ()))
        gaps.extend(reason for reason in validated.reasons if reason not in gaps)
        final_report["evidence_gaps"] = gaps
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
                validated.reasons,
            )
        report_row = InvestigationReport(
            investigation_id=investigation_id,
            result_state=validated.result_state,
            headline=str(final_report["headline"]),
            summary=str(final_report["summary"]),
            incident_cause=dict(final_report["incident_cause"]),
            code_diagnosis=dict(final_report["code_diagnosis"]),
            participants=list(final_report["participants"]),
            timeline_summary=list(final_report["timeline_summary"]),
            source_assessments=list(final_report["source_assessments"]),
            configuration_assessments=list(final_report["configuration_assessments"]),
            confirmed_facts=list(final_report["confirmed_facts"]),
            counter_evidence=list(final_report["counter_evidence"]),
            evidence_gaps=list(final_report["evidence_gaps"]),
            next_step=str(final_report["next_step"]),
            synthesizer_invocation_id=synthesizer_invocation_id,
            verifier_invocation_id=verifier_invocation_id,
            report_hash=report_hash,
        )
        self.session.add(report_row)
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
        investigation.result_state = validated.result_state
        investigation.status = "completed"
        investigation.finished_at = datetime.now(UTC)
        return PublishedReport(
            investigation_id,
            validated.result_state,
            report_hash,
            tuple(finding_ids),
            validated.reasons,
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
            "revision_role": finding.revision_role,
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
            or revision.revision_role != finding.revision_role
            or any(provenance.get(key) != value for key, value in expected.items())
        ):
            raise ReportValidationError("code_finding_source_provenance_mismatch")
        assessment = await self.session.get(SourceAssessment, finding.source_assessment_id)
        assert assessment is not None
        return SourceAuthorityAssessment(
            repository_snapshot_id=snapshot.id,
            revision_role=revision.revision_role,
            requested_ref=revision.requested_ref,
            resolved_sha=revision.resolved_sha,
            status=assessment.runtime_match_status,
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
        if (
            verifier_policy.get("separate_provider", False)
            and verifier_row.provider_account_id == synthesizer.provider_account_id
        ):
            return "rejected"
        confirmed = {
            index
            for index, finding in enumerate(payload.code_findings)
            if finding.status == "confirmed"
        }
        approved = {
            item.finding_index for item in verifier.finding_verdicts if item.verdict == "approved"
        }
        return "approved" if confirmed.issubset(approved) else "rejected"


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
    values.update(payload.incident_cause.evidence_refs)
    for finding in payload.code_findings:
        values.update(_finding_evidence_refs(finding))
    for participant in payload.participants:
        values.update(participant.evidence_refs)
    for item in payload.timeline_summary:
        values.update(item.evidence_refs)
    for item in payload.source_assessments:
        values.update(item.evidence_refs)
    for item in payload.configuration_assessments:
        values.update(item.evidence_refs)
    for item in (*payload.confirmed_facts, *payload.counter_evidence):
        values.update(item.evidence_refs)
    return frozenset(values)


def _snapshot_mismatch_reasons(snapshot: InvestigationRepositorySnapshot) -> list[str]:
    if snapshot.frozen_resolution_status == "unresolved":
        return ["source_revision_unresolved"]
    if snapshot.frozen_resolution_status == "unverified":
        return ["source_revision_not_runtime_verified"]
    if snapshot.frozen_resolution_status == "contradicted":
        return ["runtime_revision_contradicted"]
    return []
