"""Assemble model-safe evidence packages with server authority metadata."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models import EvidenceArtifact, EvidenceAssertion


async def assertions_by_artifact(
    session: AsyncSession, investigation_id: int
) -> Mapping[int, tuple[EvidenceAssertion, ...]]:
    assertions = tuple(
        (
            await session.execute(
                select(EvidenceAssertion)
                .where(EvidenceAssertion.investigation_id == investigation_id)
                .order_by(EvidenceAssertion.id)
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[int, list[EvidenceAssertion]] = defaultdict(list)
    for assertion in assertions:
        for artifact_id in assertion.supporting_evidence_refs:
            grouped[artifact_id].append(assertion)
    return {key: tuple(value) for key, value in grouped.items()}


async def model_assertion_graph(
    session: AsyncSession, investigation_id: int
) -> tuple[Mapping[str, Any], ...]:
    assertions = tuple(
        (
            await session.execute(
                select(EvidenceAssertion)
                .where(EvidenceAssertion.investigation_id == investigation_id)
                .order_by(EvidenceAssertion.id)
            )
        )
        .scalars()
        .all()
    )
    return tuple(_model_assertion(assertion) for assertion in assertions)


def model_evidence_package(
    artifact: EvidenceArtifact,
    assertions: Sequence[EvidenceAssertion] = (),
) -> Mapping[str, Any]:
    return {
        "content": artifact.content_masked,
        "provenance": artifact.provenance,
        "source_time": {
            "start": (
                artifact.source_time_start.isoformat()
                if artifact.source_time_start is not None
                else None
            ),
            "end": (
                artifact.source_time_end.isoformat()
                if artifact.source_time_end is not None
                else None
            ),
        },
        "source_revision": artifact.source_revision,
        "server_assertions": [_model_assertion(assertion) for assertion in assertions],
    }


def _model_assertion(assertion: EvidenceAssertion) -> Mapping[str, Any]:
    return {
        "assertion_id": assertion.id,
        "kind": assertion.assertion_kind,
        "status": assertion.status,
        "statement": assertion.statement,
        "structured_claim": assertion.structured_claim,
        "supporting_evidence_refs": list(assertion.supporting_evidence_refs),
        "counter_evidence_refs": list(assertion.counter_evidence_refs),
        "missing_validation": list(assertion.missing_validation),
    }
