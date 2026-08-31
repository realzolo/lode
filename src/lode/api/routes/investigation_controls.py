"""Immutable investigation controls, child runs, and run comparison APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import require_user
from lode.api.routes.investigations import _error, _investigation_access, get_session
from lode.api.types import EntityId
from lode.db.models import (
    EvidenceArtifact,
    Investigation,
    InvestigationDecision,
    InvestigationInput,
    InvestigationReport,
)
from lode.infrastructure.intake_store import PostgresIntakeStore
from lode.infrastructure.investigation_control import InvestigationControlService

router = APIRouter(prefix="/investigations", tags=["investigation-controls"])


class InvestigationControlRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4_000)


class InvestigationEvidenceRequest(BaseModel):
    description: str = Field(min_length=1, max_length=4_000)
    evidence_text: str = Field(min_length=1, max_length=100_000)


class InvestigationQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)


class InvestigationBranchRequest(BaseModel):
    hypothesis: str = Field(min_length=1, max_length=20_000)


class ChildInvestigationOut(BaseModel):
    parent_investigation_id: EntityId
    investigation_id: EntityId
    job_id: EntityId
    trigger_reason: str


@router.get("/comparisons")
async def compare_investigations(
    left_id: EntityId,
    right_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _, left = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=left_id,
        permission="viewer",
    )
    _, right = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=right_id,
        permission="viewer",
    )
    if left.incident_id != right.incident_id or left.workspace_id != right.workspace_id:
        raise _error(
            422,
            "investigation_comparison_scope_mismatch",
            "Investigation runs must belong to the same incident and Workspace.",
        )

    async def snapshot(run: Investigation) -> dict[str, Any]:
        input_row = await session.get(InvestigationInput, run.id)
        last_decision = (
            await session.execute(
                select(InvestigationDecision)
                .where(InvestigationDecision.investigation_id == run.id)
                .order_by(InvestigationDecision.ordinal.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        artifacts = tuple(
            (
                await session.execute(
                    select(EvidenceArtifact)
                    .where(EvidenceArtifact.investigation_id == run.id)
                    .order_by(EvidenceArtifact.id)
                )
            ).scalars()
        )
        report = await session.get(InvestigationReport, run.id)
        return {
            "run": {
                "id": run.id,
                "parent_investigation_id": run.parent_investigation_id,
                "trigger_reason": run.trigger_reason,
                "status": run.status,
                "result_state": run.result_state,
                "window_started_at": run.window_started_at,
                "window_finished_at": run.window_finished_at,
            },
            "input": None
            if input_row is None
            else {
                "signal_id": input_row.signal_id,
                "title": input_row.title,
                "summary": input_row.summary,
                "source_type": input_row.source_type,
                "repository_binding_id": input_row.repository_binding_id,
                "source_revision": input_row.source_revision,
                "raw_payload_masked": input_row.raw_payload_masked,
            },
            "hypotheses": [] if last_decision is None else last_decision.hypotheses,
            "evidence": [
                {
                    "id": artifact.id,
                    "artifact_kind": artifact.artifact_kind,
                    "content_hash": artifact.content_hash,
                    "source_revision": artifact.source_revision,
                }
                for artifact in artifacts
            ],
            "causal_graph": None if report is None else report.causal_graph,
            "conclusion": None
            if report is None
            else {
                "result_state": report.result_state,
                "headline": report.headline,
                "executive_summary": report.executive_summary,
                "causal_graph": report.causal_graph,
            },
        }

    return {
        "incident_id": left.incident_id,
        "left": await snapshot(left),
        "right": await snapshot(right),
    }


@router.post("/{investigation_id}/pause")
async def pause_investigation(
    investigation_id: EntityId,
    payload: InvestigationControlRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user, _ = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="operator",
    )
    try:
        result = await InvestigationControlService(session).stop(
            investigation_id=investigation_id,
            actor_id=user.id,
            command="pause",
            reason=payload.reason,
        )
    except ValueError as exc:
        raise _error(409, "investigation_control_rejected", str(exc)) from exc
    return {"id": result.investigation_id, "status": result.status}


@router.post("/{investigation_id}/cancel")
async def cancel_investigation(
    investigation_id: EntityId,
    payload: InvestigationControlRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user, _ = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="operator",
    )
    try:
        result = await InvestigationControlService(session).stop(
            investigation_id=investigation_id,
            actor_id=user.id,
            command="cancel",
            reason=payload.reason,
        )
    except ValueError as exc:
        raise _error(409, "investigation_control_rejected", str(exc)) from exc
    return {"id": result.investigation_id, "status": result.status}


async def _create_child(
    session: AsyncSession,
    *,
    parent: Investigation,
    actor_id: int,
    command: str,
    intervention: dict[str, Any],
) -> ChildInvestigationOut:
    try:
        result = await PostgresIntakeStore(session).create_child_investigation(
            parent_investigation_id=parent.id,
            created_by=actor_id,
            command=command,
            intervention=intervention,
        )
    except ValueError as exc:
        raise _error(409, "investigation_child_rejected", str(exc)) from exc
    assert result.investigation_id is not None and result.job_id is not None
    child = await session.get(Investigation, result.investigation_id)
    if child is None:
        raise RuntimeError("child investigation was not persisted")
    return ChildInvestigationOut(
        parent_investigation_id=parent.id,
        investigation_id=child.id,
        job_id=result.job_id,
        trigger_reason=child.trigger_reason,
    )


@router.post("/{investigation_id}/resume", response_model=ChildInvestigationOut, status_code=201)
async def resume_investigation(
    investigation_id: EntityId,
    payload: InvestigationControlRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ChildInvestigationOut:
    user, parent = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="operator",
    )
    return await _create_child(
        session,
        parent=parent,
        actor_id=user.id,
        command="resume",
        intervention={"reason": payload.reason},
    )


@router.post("/{investigation_id}/evidence", response_model=ChildInvestigationOut, status_code=201)
async def add_investigation_evidence(
    investigation_id: EntityId,
    payload: InvestigationEvidenceRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ChildInvestigationOut:
    user, parent = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="operator",
    )
    return await _create_child(
        session,
        parent=parent,
        actor_id=user.id,
        command="add_evidence",
        intervention=payload.model_dump(),
    )


@router.post("/{investigation_id}/questions", response_model=ChildInvestigationOut, status_code=201)
async def follow_up_investigation(
    investigation_id: EntityId,
    payload: InvestigationQuestionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ChildInvestigationOut:
    user, parent = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="operator",
    )
    return await _create_child(
        session,
        parent=parent,
        actor_id=user.id,
        command="follow_up",
        intervention=payload.model_dump(),
    )


@router.post("/{investigation_id}/branches", response_model=ChildInvestigationOut, status_code=201)
async def branch_investigation(
    investigation_id: EntityId,
    payload: InvestigationBranchRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ChildInvestigationOut:
    user, parent = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="operator",
    )
    return await _create_child(
        session,
        parent=parent,
        actor_id=user.id,
        command="branch_hypothesis",
        intervention=payload.model_dump(),
    )
