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
    EvidenceCollection,
    InvestigationInput,
    InvestigationRepositorySnapshot,
    SourceAssessment,
    SourceRevision,
)
from lode.domain.investigation import canonical_hash
from lode.domain.model_execution import SourceRevisionRole
from lode.infrastructure.git_source import GitSourceHit
from lode.masking import mask_structure
from lode.metrics import SOURCE_MISMATCH, SOURCE_RESOLUTION


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
        revision_role: SourceRevisionRole,
        requested_ref: str | None,
        resolved_sha: str | None,
        hits: Sequence[GitSourceHit],
        runtime_revision_evidence_refs: Sequence[int] = (),
        contradiction_evidence_refs: Sequence[int] = (),
    ) -> ArchivedSourceRevision:
        snapshot = await self.session.get(InvestigationRepositorySnapshot, repository_snapshot_id)
        if snapshot is None or snapshot.investigation_id != investigation_id:
            raise ValueError("repository snapshot does not belong to the investigation")
        incident_input = await self.session.get(InvestigationInput, investigation_id)
        if incident_input is None:
            raise ValueError("investigation input is missing")
        assessment = self.authority.assess(
            repository_snapshot_id=repository_snapshot_id,
            revision_role=revision_role,
            requested_ref=requested_ref,
            resolved_sha=resolved_sha,
            incident_source_revision=incident_input.source_revision,
            frozen_resolution_status=snapshot.frozen_resolution_status,
            runtime_revision_evidence_refs=runtime_revision_evidence_refs,
            contradiction_evidence_refs=contradiction_evidence_refs,
        )
        existing_revision = (
            await self.session.execute(
                select(SourceRevision).where(
                    SourceRevision.investigation_id == investigation_id,
                    SourceRevision.repository_snapshot_id == repository_snapshot_id,
                    SourceRevision.revision_role == revision_role,
                    SourceRevision.resolved_sha == resolved_sha,
                )
            )
        ).scalar_one_or_none()
        if existing_revision is not None:
            existing_assessment = (
                await self.session.execute(
                    select(SourceAssessment).where(
                        SourceAssessment.investigation_id == investigation_id,
                        SourceAssessment.source_revision_id == existing_revision.id,
                    )
                )
            ).scalar_one()
            return ArchivedSourceRevision(
                existing_revision.id,
                existing_assessment.id,
                tuple(existing_revision.source_artifact_refs),
                existing_assessment.runtime_match_status,
            )

        artifact_ids: list[int] = []
        if resolved_sha is not None and hits:
            collection_payload = {
                "repository_snapshot_id": repository_snapshot_id,
                "revision_role": revision_role,
                "resolved_sha": resolved_sha,
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
                fingerprint=canonical_hash(collection_payload),
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
                    "revision_role": revision_role,
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
                    evidence_class=revision_role,
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
        source_revision = SourceRevision(
            investigation_id=investigation_id,
            repository_snapshot_id=repository_snapshot_id,
            revision_role=revision_role,
            requested_ref=requested_ref,
            resolved_sha=resolved_sha,
            resolution_status=assessment.status,
            resolution_basis={
                "repository_snapshot_id": repository_snapshot_id,
                "requested_ref": requested_ref,
                "runtime_evidence_refs": list(assessment.runtime_evidence_refs),
                "mismatch_reasons": list(assessment.mismatch_reasons),
            },
            source_artifact_refs=artifact_ids,
        )
        self.session.add(source_revision)
        await self.session.flush()
        assessment_payload = {
            "source_revision_id": source_revision.id,
            "status": assessment.status,
            "mismatch_reasons": list(assessment.mismatch_reasons),
            "evidence_refs": list(assessment.runtime_evidence_refs),
        }
        source_assessment = SourceAssessment(
            investigation_id=investigation_id,
            source_revision_id=source_revision.id,
            build_unit_snapshot_id=None,
            component_snapshot_id=None,
            runtime_match_status=assessment.status,
            mismatch_reasons=list(assessment.mismatch_reasons),
            evidence_refs=list(assessment.runtime_evidence_refs),
            assessment_hash=canonical_hash(assessment_payload),
        )
        self.session.add(source_assessment)
        await self.session.flush()
        SOURCE_RESOLUTION.labels(status=assessment.status).inc()
        if assessment.mismatch_reasons:
            SOURCE_MISMATCH.inc()
        return ArchivedSourceRevision(
            source_revision.id,
            source_assessment.id,
            tuple(artifact_ids),
            assessment.status,
        )
