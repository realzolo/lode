"""Current manual intake, investigation read model, replay stream, and lifecycle API."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import assert_workspace_permission, permitted_workspace_ids, require_user
from lode.api.investigation_execution_graph import (
    ExecutionArtifactPage,
    InvestigationExecutionGraph,
    InvestigationExecutionNodeDetail,
    _clean_report_text,
    build_artifact_page,
    build_execution_graph,
    build_node_detail,
)
from lode.api.types import EntityId
from lode.db.models import (
    EvidenceArtifact,
    IncidentEvent,
    IncidentKnowledgeCase,
    Investigation,
    InvestigationCodeFinding,
    InvestigationInput,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationReport,
    InvestigationReview,
    User,
    Workspace,
)
from lode.db.session import AsyncSessionLocal
from lode.metrics import SSE_CONNECTIONS, SSE_REPLAY_LAG

router = APIRouter(prefix="/investigations", tags=["investigations"])
workbench_router = APIRouter(prefix="/workbench", tags=["workbench"])
_TERMINAL_STATUSES = {"paused", "completed", "failed", "cancelled"}


class InvestigationReportSummary(BaseModel):
    headline: str
    executive_summary: str
    impact_scope: list[dict]
    causal_graph: dict
    evidence_gaps: list[dict]
    action_recommendations: list[dict]


class InvestigationOverview(BaseModel):
    id: EntityId
    incident_id: EntityId
    workspace_id: EntityId
    status: str
    result_state: str
    trigger_reason: str
    output_language: str
    title: str | None
    summary: str | None
    severity: str | None
    observed_at: datetime | None
    error_type: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    report: InvestigationReportSummary | None
    operation_count: int
    evidence_count: int


class InvestigationCodeFindingView(BaseModel):
    id: EntityId
    status: str
    source_artifact_id: EntityId | None
    repository_id: EntityId | None
    revision: str | None
    revision_origin: str | None
    path: str | None
    symbol: str | None
    start_line: int | None
    end_line: int | None
    issue_type: str | None
    faulty_behavior: str
    why_wrong: str
    expected_behavior: str
    trigger_condition: str
    propagation: list[str]
    incident_evidence_refs: list[EntityId]
    supporting_evidence_refs: list[EntityId]
    counter_evidence_refs: list[EntityId]
    missing_validation: list[str]
    test_scenario: str


class InvestigationReportView(BaseModel):
    schema_version: str
    result_state: str
    headline: str
    executive_summary: str
    impact_scope: list[dict]
    causal_graph: dict
    participants: list[dict]
    timeline_summary: list[dict]
    source_assessments: list[dict]
    configuration_assessments: list[dict]
    counter_evidence: list[dict]
    evidence_gaps: list[dict]
    action_recommendations: list[dict]
    code_findings: list[InvestigationCodeFindingView]


class InvestigationReviewRequest(BaseModel):
    code_finding_id: EntityId | None = None
    verdict: str = Field(pattern="^(accepted|rejected|needs_evidence)$")
    comment: str = Field(min_length=1, max_length=20_000)
    supersedes_review_id: EntityId | None = None


class InvestigationReviewOut(BaseModel):
    id: EntityId
    code_finding_id: EntityId | None
    verdict: str
    comment: str
    reviewer_id: EntityId
    supersedes_review_id: EntityId | None
    created_at: datetime


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@workbench_router.get("/workspaces")
async def list_workbench_workspaces(
    user_id: EntityId = Depends(require_user), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    """Return all Workspaces for admins, granted Workspaces for ordinary users."""
    user = await _active_user(session, user_id)
    allowed = await permitted_workspace_ids(session, user.id, user.is_system_admin)
    if allowed is not None and not allowed:
        return []
    statement = select(Workspace).order_by(Workspace.name, Workspace.id)
    if allowed is not None:
        statement = statement.where(Workspace.id.in_(allowed))
    rows = (await session.execute(statement)).scalars().all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "ingestion_topic": row.ingestion_topic,
            "model_policy_revision_id": row.model_policy_revision_id,
            "ingestion_state": row.ingestion_state,
            "ingestion_version": row.ingestion_version,
            "ingestion_start_position": row.ingestion_start_position,
            "ingestion_started_at": row.ingestion_started_at,
            "ingestion_paused_at": row.ingestion_paused_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


def _error(status: int, code: str, message: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, **details},
    )


async def _active_user(session: AsyncSession, user_id: EntityId) -> User:
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise _error(401, "active_user_required", "An active user is required.")
    return user


async def _require_workspace(
    session: AsyncSession,
    *,
    user_id: EntityId,
    workspace_id: EntityId,
    permission: str,
) -> User:
    user = await _active_user(session, user_id)
    if await session.get(Workspace, workspace_id) is None:
        raise _error(404, "workspace_not_found", "Workspace not found.")
    await assert_workspace_permission(session, user, workspace_id, permission)
    return user


async def _investigation_access(
    session: AsyncSession,
    *,
    user_id: EntityId,
    investigation_id: EntityId,
    permission: str,
) -> tuple[User, Investigation]:
    user = await _active_user(session, user_id)
    row = await session.get(Investigation, investigation_id)
    if row is None:
        raise _error(404, "investigation_not_found", "Investigation not found.")
    await assert_workspace_permission(session, user, row.workspace_id, permission)
    return user, row


def _row(row: Any, *, exclude: frozenset[str] = frozenset()) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in exclude
    }


def _report_summary(
    report: InvestigationReport | None,
) -> InvestigationReportSummary | None:
    if report is None:
        return None
    return InvestigationReportSummary(
        headline=_clean_report_text(report.headline),
        executive_summary=_clean_report_text(report.executive_summary),
        impact_scope=report.impact_scope,
        causal_graph=report.causal_graph,
        evidence_gaps=report.evidence_gaps,
        action_recommendations=report.action_recommendations,
    )


def _positive_ids(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    return sorted(
        {
            item
            for item in value
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        }
    )




@router.get("/{investigation_id}", response_model=InvestigationOverview)
async def get_investigation(
    investigation_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="viewer",
    )
    internal_id = investigation.id
    input_row = await session.get(InvestigationInput, internal_id)
    report = await session.get(InvestigationReport, internal_id)
    error = input_row.error_masked if input_row is not None else {}
    return InvestigationOverview(
        id=investigation.id,
        incident_id=investigation.incident_id,
        workspace_id=investigation.workspace_id,
        status=investigation.status,
        result_state=investigation.result_state,
        trigger_reason=investigation.trigger_reason,
        output_language=investigation.output_language,
        title=input_row.title if input_row is not None else None,
        summary=input_row.summary if input_row is not None else None,
        severity=input_row.severity if input_row is not None else None,
        observed_at=input_row.observed_at if input_row is not None else None,
        error_type=str(error.get("type")) if error.get("type") is not None else None,
        error_message=str(error.get("message")) if error.get("message") is not None else None,
        created_at=investigation.created_at,
        updated_at=investigation.updated_at,
        report=_report_summary(report),
        operation_count=int(
            await session.scalar(
                select(func.count()).select_from(InvestigationOperation).where(
                    InvestigationOperation.investigation_id == internal_id
                )
            )
            or 0
        ),
        evidence_count=int(
            await session.scalar(
                select(func.count()).select_from(EvidenceArtifact).where(
                    EvidenceArtifact.investigation_id == internal_id
                )
            )
            or 0
        ),
    )




def _finding_view(row: InvestigationCodeFinding) -> InvestigationCodeFindingView:
    return InvestigationCodeFindingView(
        id=row.id,
        status=row.status,
        source_artifact_id=row.source_artifact_id,
        repository_id=row.repository_id,
        revision=row.revision,
        revision_origin=row.revision_origin,
        path=row.path,
        symbol=row.symbol,
        start_line=row.start_line,
        end_line=row.end_line,
        issue_type=row.issue_type,
        faulty_behavior=_clean_report_text(row.faulty_behavior),
        why_wrong=_clean_report_text(row.why_wrong),
        expected_behavior=_clean_report_text(row.expected_behavior),
        trigger_condition=_clean_report_text(row.trigger_condition),
        propagation=[_clean_report_text(value) for value in row.propagation],
        incident_evidence_refs=_positive_ids(row.incident_evidence_refs),
        supporting_evidence_refs=_positive_ids(row.supporting_evidence_refs),
        counter_evidence_refs=_positive_ids(row.counter_evidence_refs),
        missing_validation=[_clean_report_text(value) for value in row.missing_validation],
        test_scenario=_clean_report_text(row.test_scenario),
    )


@router.get("/{investigation_id}/report", response_model=InvestigationReportView)
async def get_investigation_report(
    investigation_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> InvestigationReportView:
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="viewer",
    )
    report = await session.get(InvestigationReport, investigation.id)
    if report is None:
        raise _error(404, "investigation_report_not_found", "Investigation report is not available.")
    findings = tuple(
        (
            await session.execute(
                select(InvestigationCodeFinding)
                .where(InvestigationCodeFinding.investigation_id == investigation.id)
                .order_by(InvestigationCodeFinding.id)
            )
        ).scalars()
    )
    return InvestigationReportView(
        schema_version=report.schema_version,
        result_state=report.result_state,
        headline=_clean_report_text(report.headline),
        executive_summary=_clean_report_text(report.executive_summary),
        impact_scope=report.impact_scope,
        causal_graph=report.causal_graph,
        participants=report.participants,
        timeline_summary=report.timeline_summary,
        source_assessments=report.source_assessments,
        configuration_assessments=report.configuration_assessments,
        counter_evidence=report.counter_evidence,
        evidence_gaps=report.evidence_gaps,
        action_recommendations=report.action_recommendations,
        code_findings=[_finding_view(row) for row in findings],
    )


@router.get("/{investigation_id}/reviews", response_model=list[InvestigationReviewOut])
async def list_investigation_reviews(
    investigation_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[InvestigationReviewOut]:
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="viewer",
    )
    rows = tuple(
        (
            await session.execute(
                select(InvestigationReview)
                .where(InvestigationReview.investigation_id == investigation.id)
                .order_by(InvestigationReview.created_at, InvestigationReview.id)
            )
        ).scalars()
    )
    return [
        InvestigationReviewOut(
            id=row.id,
            code_finding_id=row.code_finding_id,
            verdict=row.verdict,
            comment=row.comment,
            reviewer_id=row.reviewer_id,
            supersedes_review_id=row.supersedes_review_id,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/{investigation_id}/reviews", response_model=InvestigationReviewOut, status_code=201)
async def create_investigation_review(
    investigation_id: EntityId,
    payload: InvestigationReviewRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> InvestigationReviewOut:
    user, investigation = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="operator",
    )
    if payload.code_finding_id is not None:
        finding = await session.get(InvestigationCodeFinding, payload.code_finding_id)
        if finding is None or finding.investigation_id != investigation.id:
            raise _error(422, "review_finding_mismatch", "Code finding does not belong to investigation.")
    if payload.supersedes_review_id is not None:
        superseded = await session.get(InvestigationReview, payload.supersedes_review_id)
        if superseded is None or superseded.investigation_id != investigation.id:
            raise _error(
                422,
                "superseded_review_mismatch",
                "Superseded review does not belong to investigation.",
            )
        already_superseded = await session.scalar(
            select(InvestigationReview.id).where(
                InvestigationReview.supersedes_review_id == superseded.id
            )
        )
        if already_superseded is not None:
            raise _error(409, "review_already_superseded", "Review was already superseded.")
    review = InvestigationReview(
        investigation_id=investigation.id,
        code_finding_id=payload.code_finding_id,
        verdict=payload.verdict,
        comment=payload.comment,
        reviewer_id=user.id,
        supersedes_review_id=payload.supersedes_review_id,
    )
    session.add(review)
    await session.flush()
    if payload.verdict == "accepted" and payload.code_finding_id is None:
        report = await session.get(InvestigationReport, investigation.id)
        if report is None:
            raise _error(
                409,
                "report_review_requires_report",
                "A report-level acceptance requires a published report.",
            )
        existing_case = await session.scalar(
            select(IncidentKnowledgeCase.id).where(
                IncidentKnowledgeCase.investigation_id == investigation.id
            )
        )
        if existing_case is None:
            nodes = report.causal_graph.get("nodes", [])
            edges = report.causal_graph.get("edges", [])
            signature = sorted(
                {
                    *(str(node.get("node_type")) for node in nodes if isinstance(node, dict)),
                    *(str(edge.get("relation")) for edge in edges if isinstance(edge, dict)),
                }
            )
            session.add(
                IncidentKnowledgeCase(
                    workspace_id=investigation.workspace_id,
                    incident_id=investigation.incident_id,
                    investigation_id=investigation.id,
                    accepted_review_id=review.id,
                    report_hash=report.report_hash,
                    headline=report.headline,
                    executive_summary=report.executive_summary,
                    causal_signature=signature,
                    search_document=f"{report.headline}\n{report.executive_summary}".lower(),
                )
            )
    session.add(
        IncidentEvent(
            incident_id=investigation.incident_id,
            event_type="review_recorded",
            actor_id=user.id,
            payload={
                "review_id": review.id,
                "investigation_id": investigation.id,
                "code_finding_id": review.code_finding_id,
                "verdict": review.verdict,
            },
        )
    )
    await session.commit()
    await session.refresh(review)
    return InvestigationReviewOut(
        id=review.id,
        code_finding_id=review.code_finding_id,
        verdict=review.verdict,
        comment=review.comment,
        reviewer_id=review.reviewer_id,
        supersedes_review_id=review.supersedes_review_id,
        created_at=review.created_at,
    )


@router.get(
    "/{investigation_id}/execution-graph",
    response_model=InvestigationExecutionGraph,
)
async def get_investigation_execution_graph(
    investigation_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> InvestigationExecutionGraph:
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="viewer",
    )
    return await build_execution_graph(session, investigation)


@router.get(
    "/{investigation_id}/execution-graph/nodes/{node_id}",
    response_model=InvestigationExecutionNodeDetail,
)
async def get_investigation_execution_node(
    investigation_id: EntityId,
    node_id: str,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> InvestigationExecutionNodeDetail:
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="viewer",
    )
    detail = await build_node_detail(session, investigation, node_id)
    if detail is None:
        raise _error(404, "execution_node_not_found", "Execution node not found.")
    return detail


@router.get(
    "/{investigation_id}/execution-graph/nodes/{node_id}/artifacts/{artifact_id}",
    response_model=ExecutionArtifactPage,
)
async def get_investigation_execution_artifact(
    investigation_id: EntityId,
    node_id: str,
    artifact_id: EntityId,
    after_index: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ExecutionArtifactPage:
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        investigation_id=investigation_id,
        permission="viewer",
    )
    page = await build_artifact_page(
        session,
        investigation,
        node_id,
        artifact_id,
        after_index=after_index,
        limit=limit,
    )
    if page is None:
        raise _error(404, "execution_artifact_not_found", "Execution artifact not found.")
    return page


@router.get("/{investigation_id}/events")
async def get_investigation_events(
    investigation_id: EntityId,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, investigation = await _investigation_access(
        session, user_id=user_id, investigation_id=investigation_id, permission="viewer"
    )
    values = (
        await session.execute(
            select(InvestigationOperationEvent)
            .where(
                InvestigationOperationEvent.investigation_id == investigation.id,
                InvestigationOperationEvent.sequence > after,
            )
            .order_by(InvestigationOperationEvent.sequence)
            .limit(limit)
        )
    ).scalars()
    return [_row(value) for value in values]


@router.get("/{investigation_id}/stream")
async def stream_investigation(
    investigation_id: EntityId,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user_id: EntityId = Depends(require_user),
):
    async with AsyncSessionLocal() as access_session:
        _, investigation = await _investigation_access(
            access_session,
            user_id=user_id,
            investigation_id=investigation_id,
            permission="viewer",
        )
    try:
        header_cursor = int(last_event_id or "0")
    except ValueError as exc:
        raise _error(422, "event_cursor_invalid", "Last-Event-ID must be an integer.") from exc
    cursor = max(after, header_cursor)
    internal_id = investigation.id

    async def events():
        nonlocal cursor
        SSE_CONNECTIONS.inc()
        SSE_REPLAY_LAG.observe(max(0, investigation.event_cursor - cursor))
        try:
            while not await request.is_disconnected():
                async with AsyncSessionLocal() as stream_session:
                    values = tuple(
                        (
                            await stream_session.execute(
                                select(InvestigationOperationEvent)
                                .where(
                                    InvestigationOperationEvent.investigation_id == internal_id,
                                    InvestigationOperationEvent.sequence > cursor,
                                )
                                .order_by(InvestigationOperationEvent.sequence)
                                .limit(100)
                            )
                        ).scalars()
                    )
                    current = await stream_session.get(Investigation, internal_id)
                for value in values:
                    cursor = value.sequence
                    payload = json.dumps(
                        jsonable_encoder(_row(value)), separators=(",", ":")
                    )
                    yield f"id: {cursor}\nevent: {value.event_name}\ndata: {payload}\n\n"
                if values:
                    continue
                if current is None:
                    return
                if current.status in _TERMINAL_STATUSES:
                    state = json.dumps(
                        jsonable_encoder(
                            {
                                "id": current.id,
                                "status": current.status,
                                "result_state": current.result_state,
                                "event_cursor": current.event_cursor,
                            }
                        ),
                        separators=(",", ":"),
                    )
                    yield f"event: investigation.finished\ndata: {state}\n\n"
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(1)
        finally:
            SSE_CONNECTIONS.dec()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
