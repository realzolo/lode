"""Archive normalized native-read results before immutable attempts are committed."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models import (
    AuthorizedEvidenceRead,
    EvidenceAccessDecision,
    EvidenceArtifact,
    EvidenceCollection,
    NativeReadCandidate,
)
from lode.domain.investigation import canonical_hash
from lode.masking import mask_structure


class PostgresEvidenceResultArchiver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def archive(
        self,
        authorized: AuthorizedEvidenceRead,
        decision: EvidenceAccessDecision,
        result: Mapping[str, Any],
    ) -> tuple[int, ...]:
        candidate = await self.session.get(NativeReadCandidate, decision.candidate_id)
        if candidate is None or candidate.investigation_id != authorized.investigation_id:
            raise ValueError("native read candidate ownership failed during archive")
        masked, categories = mask_structure(result)
        if not isinstance(masked, dict):
            raise TypeError("normalized evidence result must be an object")
        result_bytes = len(
            json.dumps(masked, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        collection = EvidenceCollection(
            investigation_id=authorized.investigation_id,
            operation_id=candidate.operation_id,
            connector_snapshot_id=candidate.connector_snapshot_id,
            collection_kind="native_read",
            status="succeeded",
            fingerprint=authorized.fingerprint,
            purpose=candidate.purpose,
            selector_masked={
                "language": candidate.language,
                "payload": candidate.payload_masked,
                "candidate_hash": candidate.candidate_hash,
            },
            artifact_count=1,
            result_bytes=result_bytes,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        self.session.add(collection)
        await self.session.flush()
        artifact = EvidenceArtifact(
            investigation_id=authorized.investigation_id,
            collection_id=collection.id,
            artifact_kind=_artifact_kind(candidate.language),
            evidence_class="runtime",
            content_masked=masked,
            content_hash=canonical_hash(masked),
            provenance={
                "authorized_read_id": authorized.id,
                "access_decision_id": decision.id,
                "candidate_id": candidate.id,
                "candidate_hash": candidate.candidate_hash,
                "masking_categories": list(categories),
            },
            source_time_start=None,
            source_time_end=None,
            source_revision=None,
            data_class="masked",
            prompt_injection_markers=(
                [{"detected": True}]
                if result.get("prompt_injection_detected") is True
                else []
            ),
        )
        self.session.add(artifact)
        await self.session.flush()
        return (artifact.id,)


def _artifact_kind(language: str) -> str:
    return {
        "logql": "normalized_log_result",
        "elasticsearch_query_dsl": "normalized_search_result",
        "opensearch_query_dsl": "normalized_search_result",
        "sql": "normalized_sql_result",
        "https": "normalized_https_result",
        "command": "normalized_command_result",
    }[language]
