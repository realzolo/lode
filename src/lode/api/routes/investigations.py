"""Current manual intake, investigation read model, replay stream, and lifecycle API."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Text, cast, func, or_, select
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
from lode.application.intake import ManualIncidentRequest, NormalizedIncident, normalize_manual
from lode.crypto import decrypt_value
from lode.db.models import (
    AuditEvent,
    EvidenceArtifact,
    Investigation,
    InvestigationCodeFinding,
    InvestigationInput,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationReport,
    SealedEvidenceValue,
    User,
    Workspace,
)
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.intake_store import PostgresIntakeStore
from lode.metrics import SSE_CONNECTIONS, SSE_REPLAY_LAG

router = APIRouter(prefix="/investigations", tags=["investigations"])
workbench_router = APIRouter(prefix="/workbench", tags=["workbench"])
_TERMINAL_STATUSES = {"completed", "failed"}


class ManualInvestigationCreated(BaseModel):
    id: EntityId
    workspace_id: EntityId
    status: str
    job_id: EntityId


class InvestigationListItem(BaseModel):
    id: EntityId
    workspace_id: EntityId
    status: str
    result_state: str
    output_language: str
    event: str | None = None
    severity: str | None = None
    headline: str | None = None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InvestigationListPage(BaseModel):
    items: list[InvestigationListItem]
    next_after_id: EntityId | None


class InvestigationReportConclusion(BaseModel):
    status: str
    summary: str
    causal_chain: list[str]
    evidence_refs: list[EntityId]


class InvestigationReportFact(BaseModel):
    text: str
    evidence_refs: list[EntityId]


class InvestigationReportSummary(BaseModel):
    headline: str
    summary: str
    cause: InvestigationReportConclusion
    code_diagnosis: InvestigationReportConclusion
    confirmed_facts: list[InvestigationReportFact]
    evidence_gaps: list[str]
    next_step: str


class InvestigationOverview(BaseModel):
    id: EntityId
    workspace_id: EntityId
    status: str
    result_state: str
    output_language: str
    archived_at: datetime | None
    event: str | None
    severity: str | None
    occurred_at: datetime | None
    error_type: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    report: InvestigationReportSummary | None
    operation_count: int
    evidence_count: int


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
    code_findings: tuple[InvestigationCodeFinding, ...] = (),
) -> InvestigationReportSummary | None:
    if report is None:
        return None
    cause = report.incident_cause
    diagnosis = report.code_diagnosis
    facts = []
    for value in report.confirmed_facts:
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            continue
        facts.append(
            InvestigationReportFact(
                text=_clean_report_text(value["text"]),
                evidence_refs=_positive_ids(value.get("evidence_refs")),
            )
        )
    return InvestigationReportSummary(
        headline=_clean_report_text(report.headline),
        summary=_clean_report_text(report.summary),
        cause=InvestigationReportConclusion(
            status=str(cause.get("status", "not_found")),
            summary=_clean_report_text(str(cause.get("mechanism", ""))),
            causal_chain=[
                _clean_report_text(str(item)) for item in cause.get("causal_chain", [])
            ],
            evidence_refs=_positive_ids(cause.get("evidence_refs")),
        ),
        code_diagnosis=InvestigationReportConclusion(
            status=str(diagnosis.get("status", "not_found")),
            summary=_clean_report_text(str(diagnosis.get("summary", ""))),
            causal_chain=[],
            evidence_refs=_diagnosis_evidence_refs(report, code_findings),
        ),
        confirmed_facts=facts,
        evidence_gaps=[_clean_report_text(str(item)) for item in report.evidence_gaps],
        next_step=_clean_report_text(report.next_step),
    )


def _diagnosis_evidence_refs(
    report: InvestigationReport,
    code_findings: tuple[InvestigationCodeFinding, ...],
) -> list[int]:
    selected_findings = set(_positive_ids(report.code_diagnosis.get("finding_refs")))
    values: set[int] = set()
    for finding in code_findings:
        if finding.id not in selected_findings:
            continue
        values.update(_positive_ids(finding.incident_evidence_refs))
        values.update(_positive_ids(finding.supporting_evidence_refs))
        values.update(_positive_ids(finding.counter_evidence_refs))
        if finding.source_artifact_id is not None:
            values.add(finding.source_artifact_id)
    return sorted(values)


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


@router.post("", response_model=ManualInvestigationCreated, status_code=201)
async def create_manual_investigation(
    payload: ManualIncidentRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ManualInvestigationCreated:
    user = await _require_workspace(
        session,
        user_id=user_id,
        workspace_id=payload.workspace_id,
        permission="operator",
    )
    result = await PostgresIntakeStore(session).persist_manual(
        workspace_id=payload.workspace_id,
        incident=normalize_manual(payload),
        created_by=user.id,
    )
    session.add(
        AuditEvent(
            actor_id=user.id,
            actor_username=user.username,
            action="investigation.create.manual",
            target_type="investigation",
            target_id=str(result.investigation_id),
            workspace_id=payload.workspace_id,
            result="ok",
            detail={"source_type": "manual"},
        )
    )
    await session.commit()
    return ManualInvestigationCreated(
        id=result.investigation_id or 0,
        workspace_id=payload.workspace_id,
        status="queued",
        job_id=result.job_id or 0,
    )


@router.get("", response_model=InvestigationListPage)
async def list_investigations(
    workspace_id: EntityId | None = Query(default=None, gt=0),
    status: str | None = Query(default=None),
    result_state: str | None = Query(default=None),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    include_archived: bool = Query(default=False),
    after_id: EntityId | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    allowed = await permitted_workspace_ids(session, user.id, user.is_system_admin)
    statement = select(Investigation)
    if after_id is not None:
        statement = statement.where(Investigation.id > after_id)
    if allowed is not None:
        if not allowed:
            return InvestigationListPage(items=[], next_after_id=None)
        statement = statement.where(Investigation.workspace_id.in_(allowed))
    if workspace_id is not None:
        if allowed is not None and workspace_id not in allowed:
            raise _error(403, "workspace_read_forbidden", "Workspace read permission required.")
        statement = statement.where(Investigation.workspace_id == workspace_id)
    if status is not None:
        statement = statement.where(Investigation.status == status)
    if result_state is not None:
        statement = statement.where(Investigation.result_state == result_state)
    if not include_archived:
        statement = statement.where(Investigation.archived_at.is_(None))
    statement = statement.outerjoin(
        InvestigationInput, InvestigationInput.investigation_id == Investigation.id
    ).outerjoin(InvestigationReport, InvestigationReport.investigation_id == Investigation.id)
    if q is not None:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(
                cast(Investigation.id, Text).ilike(pattern),
                InvestigationInput.event.ilike(pattern),
                InvestigationReport.headline.ilike(pattern),
            )
        )
    values = (
        await session.execute(
            statement.with_only_columns(Investigation, InvestigationInput, InvestigationReport)
            .order_by(Investigation.id)
            .limit(limit + 1)
        )
    ).all()
    page = values[:limit]
    return InvestigationListPage(
        items=[
            InvestigationListItem(
                id=investigation.id,
                workspace_id=investigation.workspace_id,
                status=investigation.status,
                result_state=investigation.result_state,
                output_language=investigation.output_language,
                event=input_row.event if input_row is not None else None,
                severity=input_row.severity if input_row is not None else None,
                headline=report.headline if report is not None else None,
                archived_at=investigation.archived_at,
                created_at=investigation.created_at,
                updated_at=investigation.updated_at,
            )
            for investigation, input_row, report in page
        ],
        next_after_id=page[-1][0].id if len(values) > limit and page else None,
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
    code_findings: tuple[InvestigationCodeFinding, ...] = ()
    if report is not None:
        finding_ids = _positive_ids(report.code_diagnosis.get("finding_refs"))
        if finding_ids:
            code_findings = tuple(
                (
                    await session.execute(
                        select(InvestigationCodeFinding)
                        .where(
                            InvestigationCodeFinding.investigation_id == internal_id,
                            InvestigationCodeFinding.id.in_(finding_ids),
                        )
                        .order_by(InvestigationCodeFinding.id)
                    )
                )
                .scalars()
                .all()
            )
    error = input_row.error if input_row is not None else {}
    return InvestigationOverview(
        id=investigation.id,
        workspace_id=investigation.workspace_id,
        status=investigation.status,
        result_state=investigation.result_state,
        output_language=investigation.output_language,
        archived_at=investigation.archived_at,
        event=input_row.event if input_row is not None else None,
        severity=input_row.severity if input_row is not None else None,
        occurred_at=input_row.occurred_at if input_row is not None else None,
        error_type=str(error.get("type")) if error.get("type") is not None else None,
        error_message=str(error.get("message")) if error.get("message") is not None else None,
        created_at=investigation.created_at,
        updated_at=investigation.updated_at,
        report=_report_summary(report, code_findings),
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


@router.post(
    "/{investigation_id}/retry", response_model=ManualInvestigationCreated, status_code=201
)
async def retry_investigation(
    investigation_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, investigation = await _investigation_access(
        session, user_id=user_id, investigation_id=investigation_id, permission="operator"
    )
    if investigation.status not in _TERMINAL_STATUSES:
        raise _error(409, "investigation_not_terminal", "Only a terminal investigation can retry.")
    if investigation.archived_at is not None:
        raise _error(409, "investigation_archived", "An archived investigation cannot retry.")
    input_row = await session.get(InvestigationInput, investigation.id)
    if input_row is None:
        raise _error(
            409, "investigation_input_missing", "Immutable investigation input is missing."
        )
    trace_id = None
    if input_row.trace_value_ref is not None:
        sealed = await session.scalar(
            select(SealedEvidenceValue).where(
                SealedEvidenceValue.investigation_id == investigation.id,
                SealedEvidenceValue.value_ref == input_row.trace_value_ref,
            )
        )
        if sealed is None:
            raise _error(409, "investigation_trace_missing", "Immutable trace input is missing.")
        trace_id = decrypt_value(sealed.value_ciphertext)
    incident = NormalizedIncident(
        source_type=input_row.source_type,
        alert_id=None,
        occurred_at=input_row.occurred_at,
        severity=input_row.severity,
        event=input_row.event,
        trace_id=trace_id,
        source_revision=input_row.source_revision,
        error_masked=input_row.error,
        raw_payload_masked=input_row.raw_payload_masked,
        attachments_masked=tuple(input_row.attachments_masked),
        masking_categories=(),
    )
    result = await PostgresIntakeStore(session).persist_manual(
        workspace_id=investigation.workspace_id,
        incident=incident,
        created_by=user.id,
        retry_of_id=investigation.id,
    )
    session.add(
        AuditEvent(
            actor_id=user.id,
            actor_username=user.username,
            action="investigation.retry",
            target_type="investigation",
            target_id=str(result.investigation_id),
            workspace_id=investigation.workspace_id,
            result="ok",
            detail={"retry_of": investigation.id},
        )
    )
    await session.commit()
    return ManualInvestigationCreated(
        id=result.investigation_id or 0,
        workspace_id=investigation.workspace_id,
        status="queued",
        job_id=result.job_id or 0,
    )
