"""Immutable source evidence and source-authority persistence."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.source_authority import SourceAuthorityEngine
from lode.db.models import (
    EvidenceArtifact,
    EvidenceAssertion,
    EvidenceCollection,
    InvestigationInput,
    InvestigationRepositorySnapshot,
    SourceAssessment,
    SourceRevision,
)
from lode.domain.investigation import canonical_hash
from lode.domain.model_execution import SourceRevisionOrigin
from lode.infrastructure.git_source import GitSourceHit
from lode.masking import mask_structure
from lode.metrics import SOURCE_INCOMPATIBILITY, SOURCE_RESOLUTION


@dataclass(frozen=True, slots=True)
class ArchivedSourceRevision:
    source_revision_id: int
    source_assessment_id: int
    artifact_ids: tuple[int, ...]
    status: str


class PostgresSourceStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.authority = SourceAuthorityEngine()

    async def archive(
        self,
        *,
        investigation_id: int,
        operation_id: int | None,
        repository_snapshot_id: int,
        revision_origin: SourceRevisionOrigin,
        requested_ref: str | None,
        resolved_sha: str | None,
        hits: Sequence[GitSourceHit],
        query_fingerprint: str | None = None,
        source_query: dict[str, object] | None = None,
        runtime_revision_evidence_refs: Sequence[int] = (),
        contradiction_evidence_refs: Sequence[int] = (),
    ) -> ArchivedSourceRevision:
        snapshot = await self.session.get(InvestigationRepositorySnapshot, repository_snapshot_id)
        if snapshot is None or snapshot.investigation_id != investigation_id:
            raise ValueError("repository snapshot does not belong to the investigation")
        incident_input = await self.session.get(InvestigationInput, investigation_id)
        if incident_input is None:
            raise ValueError("investigation input is missing")
        runtime_assertions = tuple(
            (
                await self.session.execute(
                    select(EvidenceAssertion).where(
                        EvidenceAssertion.investigation_id == investigation_id,
                        EvidenceAssertion.structured_claim["claim_type"].astext
                        == "runtime_revision_observed",
                        EvidenceAssertion.structured_claim["repository_snapshot_id"].astext
                        == str(repository_snapshot_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        contradictions = list(contradiction_evidence_refs)
        contradiction_reasons: list[str] = []
        if resolved_sha is not None:
            for assertion in runtime_assertions:
                runtime_sha = assertion.structured_claim.get("runtime_observed_sha")
                if isinstance(runtime_sha, str) and runtime_sha != resolved_sha:
                    contradictions.extend(assertion.supporting_evidence_refs)
                    contradiction_reasons.append("runtime_revision_conflict")
        if (
            resolved_sha is not None
            and not hits
            and source_query is not None
            and (source_query.get("symbols") or source_query.get("path_hints"))
        ):
            evidence_refs = [
                value
                for value in source_query.get("evidence_refs", ())
                if isinstance(value, int) and value > 0
            ]
            claim = {
                "claim_type": "source_snapshot_incompatible",
                "server_generated": True,
                "repository_snapshot_id": repository_snapshot_id,
                "revision": resolved_sha,
                "symbols": list(source_query.get("symbols", ())),
                "path_hints": list(source_query.get("path_hints", ())),
            }
            assertion_hash = canonical_hash(
                {
                    "investigation_id": investigation_id,
                    "claim": claim,
                    "evidence_refs": evidence_refs,
                }
            )
            incompatibility = (
                await self.session.execute(
                    select(EvidenceAssertion).where(
                        EvidenceAssertion.investigation_id == investigation_id,
                        EvidenceAssertion.assertion_hash == assertion_hash,
                    )
                )
            ).scalar_one_or_none()
            if incompatibility is None:
                incompatibility = EvidenceAssertion(
                    investigation_id=investigation_id,
                    assertion_kind="gap",
                    status="confirmed",
                    statement=(
                        "Evidence-grounded exact source anchors were absent from the frozen "
                        "repository snapshot."
                    ),
                    structured_claim=claim,
                    supporting_evidence_refs=evidence_refs,
                    counter_evidence_refs=[],
                    missing_validation=[],
                    assertion_hash=assertion_hash,
                )
                self.session.add(incompatibility)
                await self.session.flush()
            contradictions.extend(evidence_refs)
            contradiction_reasons.append("source_snapshot_incompatible")
        assessment = self.authority.assess(
            repository_snapshot_id=repository_snapshot_id,
            revision_origin=revision_origin,
            requested_ref=requested_ref,
            resolved_sha=resolved_sha,
            incident_source_revision=incident_input.source_revision,
            runtime_revision_evidence_refs=runtime_revision_evidence_refs,
            contradiction_evidence_refs=tuple(dict.fromkeys(contradictions)),
            contradiction_reasons=tuple(dict.fromkeys(contradiction_reasons)),
        )
        existing_revision = (
            await self.session.execute(
                select(SourceRevision)
                .where(
                    SourceRevision.investigation_id == investigation_id,
                    SourceRevision.repository_snapshot_id == repository_snapshot_id,
                    SourceRevision.revision_origin == revision_origin,
                    SourceRevision.resolved_sha == resolved_sha,
                )
                .order_by(SourceRevision.id)
                .limit(1)
            )
        ).scalar_one_or_none()

        artifact_ids: list[int] = []
        if resolved_sha is not None:
            collection_payload = {
                "repository_snapshot_id": repository_snapshot_id,
                "revision_origin": revision_origin,
                "resolved_sha": resolved_sha,
                "source_query": source_query or {},
                "paths": [
                    {
                        "path": hit.path,
                        "start_line": hit.start_line,
                        "end_line": hit.end_line,
                    }
                    for hit in hits
                ],
            }
            collection = EvidenceCollection(
                investigation_id=investigation_id,
                operation_id=operation_id,
                connector_snapshot_id=None,
                collection_kind="source",
                status="running",
                fingerprint=query_fingerprint or canonical_hash(collection_payload),
                purpose="Archive bounded source excerpts at the frozen revision.",
                selector_masked=collection_payload,
                started_at=datetime.now(UTC),
            )
            self.session.add(collection)
            await self.session.flush()
            result_bytes = 0
            for hit in hits:
                masked, categories = mask_structure(
                    {
                        "path": hit.path,
                        "symbol": hit.symbol,
                        "start_line": hit.start_line,
                        "end_line": hit.end_line,
                        "content": hit.content,
                    }
                )
                provenance = {
                    "repository_snapshot_id": repository_snapshot_id,
                    "repository_id": snapshot.repository_id,
                    "revision_origin": revision_origin,
                    "revision": resolved_sha,
                    "path": hit.path,
                    "symbol": hit.symbol,
                    "start_line": hit.start_line,
                    "end_line": hit.end_line,
                    "selection_reason": hit.selection_reason,
                    "masking_categories": list(categories),
                }
                artifact = EvidenceArtifact(
                    investigation_id=investigation_id,
                    collection_id=collection.id,
                    artifact_kind="source_file",
                    evidence_class=revision_origin,
                    content_masked=masked,
                    content_hash=canonical_hash(masked),
                    provenance=provenance,
                    source_revision=resolved_sha,
                    data_class="source_code",
                    prompt_injection_markers=[],
                )
                self.session.add(artifact)
                await self.session.flush()
                artifact_ids.append(artifact.id)
                result_bytes += len(
                    json.dumps(masked, ensure_ascii=False, sort_keys=True).encode("utf-8")
                )
            collection.status = "succeeded"
            collection.artifact_count = len(artifact_ids)
            collection.result_bytes = result_bytes
            collection.finished_at = datetime.now(UTC)
        if existing_revision is None:
            source_revision = SourceRevision(
                investigation_id=investigation_id,
                repository_snapshot_id=repository_snapshot_id,
                revision_origin=revision_origin,
                requested_ref=requested_ref,
                resolved_sha=resolved_sha,
                authority_status=assessment.authority_status,
                compatibility_status=assessment.compatibility_status,
                resolution_basis={
                    "repository_snapshot_id": repository_snapshot_id,
                    "requested_ref": requested_ref,
                    "runtime_evidence_refs": list(assessment.runtime_evidence_refs),
                    "mismatch_reasons": list(assessment.mismatch_reasons),
                },
            )
            self.session.add(source_revision)
            await self.session.flush()
            assessment_payload = {
                "source_revision_id": source_revision.id,
                "authority_status": assessment.authority_status,
                "compatibility_status": assessment.compatibility_status,
                "mismatch_reasons": list(assessment.mismatch_reasons),
                "evidence_refs": list(assessment.runtime_evidence_refs),
            }
            source_assessment = SourceAssessment(
                investigation_id=investigation_id,
                source_revision_id=source_revision.id,
                build_unit_snapshot_id=None,
                component_snapshot_id=None,
                authority_status=assessment.authority_status,
                compatibility_status=assessment.compatibility_status,
                mismatch_reasons=list(assessment.mismatch_reasons),
                evidence_refs=list(assessment.runtime_evidence_refs),
                assessment_hash=canonical_hash(assessment_payload),
            )
            self.session.add(source_assessment)
            await self.session.flush()
            SOURCE_RESOLUTION.labels(status=assessment.authority_status).inc()
            if assessment.mismatch_reasons:
                SOURCE_INCOMPATIBILITY.inc()
        else:
            source_revision = existing_revision
            source_assessment = (
                await self.session.execute(
                    select(SourceAssessment).where(
                        SourceAssessment.investigation_id == investigation_id,
                        SourceAssessment.source_revision_id == existing_revision.id,
                    )
                )
            ).scalar_one()
            if (
                assessment.compatibility_status == "incompatible"
                and source_assessment.compatibility_status != "incompatible"
            ):
                source_revision.authority_status = assessment.authority_status
                source_revision.compatibility_status = assessment.compatibility_status
                source_revision.resolution_basis = {
                    **source_revision.resolution_basis,
                    "runtime_evidence_refs": list(assessment.runtime_evidence_refs),
                    "mismatch_reasons": list(assessment.mismatch_reasons),
                }
                source_assessment.authority_status = assessment.authority_status
                source_assessment.compatibility_status = assessment.compatibility_status
                source_assessment.mismatch_reasons = list(assessment.mismatch_reasons)
                source_assessment.evidence_refs = list(assessment.runtime_evidence_refs)
                source_assessment.assessment_hash = canonical_hash(
                    {
                        "source_revision_id": source_revision.id,
                        "authority_status": assessment.authority_status,
                        "compatibility_status": assessment.compatibility_status,
                        "mismatch_reasons": list(assessment.mismatch_reasons),
                        "evidence_refs": list(assessment.runtime_evidence_refs),
                    }
                )
        return ArchivedSourceRevision(
            source_revision.id,
            source_assessment.id,
            tuple(artifact_ids),
            source_assessment.authority_status,
        )
