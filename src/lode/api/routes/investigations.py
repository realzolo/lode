"""Current manual intake, investigation read model, replay stream, and lifecycle API."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import assert_workspace_permission, permitted_workspace_ids, require_user
from lode.application.intake import ManualIncidentRequest, NormalizedIncident, normalize_manual
from lode.crypto import decrypt_value
from lode.db.models import (
    AIInvocation,
    AuditEvent,
    AuthorizedEvidenceRead,
    ContextBundleRevision,
    EvidenceAccessDecision,
    EvidenceArtifact,
    EvidenceAssertion,
    EvidenceCollection,
    EvidenceReadAttempt,
    Investigation,
    InvestigationCodeFinding,
    InvestigationConnectorSnapshot,
    InvestigationDecision,
    InvestigationInput,
    InvestigationModelBindingSnapshot,
    InvestigationModelPolicySnapshot,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationReport,
    InvestigationRepositorySnapshot,
    InvestigationResourceGraphSnapshot,
    InvestigationStep,
    ModelRoutingDecision,
    NativeReadCandidate,
    ObservedEntity,
    ObservedEvent,
    ObservedRelation,
    SealedEvidenceValue,
    SourceAssessment,
    SourceRevision,
    User,
    Workspace,
)
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.intake_store import PostgresIntakeStore
from lode.metrics import SSE_CONNECTIONS, SSE_REPLAY_LAG

router = APIRouter(prefix="/investigations", tags=["investigations"])
_TERMINAL_STATUSES = {"completed", "failed"}


class ManualInvestigationCreated(BaseModel):
    id: str
    workspace_id: int
    status: str
    job_id: int


class InvestigationListItem(BaseModel):
    id: int
    public_id: str
    workspace_id: int
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
    next_after_id: int | None


class InvestigationTimelineItem(BaseModel):
    ordinal: int
    kind: str
    purpose: str
    expected_evidence: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    failure_code: str | None


class EvidenceSummaryItem(BaseModel):
    id: int
    kind: str
    evidence_class: str
    data_class: str
    source_revision: str | None
    source_time_start: datetime | None
    source_time_end: datetime | None


class InvestigationReportSummary(BaseModel):
    headline: str
    summary: str
    cause_status: str
    cause: str
    causal_chain: list[str]
    diagnosis_status: str
    diagnosis: str
    confirmed_facts: list[str]
    evidence_gaps: list[str]
    next_step: str


class InvestigationOverview(BaseModel):
    id: int
    public_id: str
    workspace_id: int
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
    timeline: list[InvestigationTimelineItem]
    evidence: list[EvidenceSummaryItem]
    operation_count: int
    evidence_count: int


AuditKind = Literal[
    "native_read_candidates",
    "access_decisions",
    "authorized_reads",
    "read_attempts",
    "ai_invocations",
]


class InvestigationAuditItem(BaseModel):
    id: int
    kind: AuditKind
    status: str
    summary: str
    created_at: datetime


class InvestigationAuditPage(BaseModel):
    items: list[InvestigationAuditItem]
    next_after_id: int | None


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def _error(status: int, code: str, message: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, **details},
    )


async def _active_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise _error(401, "active_user_required", "An active user is required.")
    return user


async def _require_workspace(
    session: AsyncSession,
    *,
    user_id: int,
    workspace_id: int,
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
    user_id: int,
    public_id: str,
    permission: str,
) -> tuple[User, Investigation]:
    user = await _active_user(session, user_id)
    row = await session.scalar(select(Investigation).where(Investigation.public_id == public_id))
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


async def _rows(
    session: AsyncSession,
    model: Any,
    investigation_id: int,
    *,
    order_by: Any | None = None,
    exclude: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    statement = select(model).where(model.investigation_id == investigation_id)
    if order_by is not None:
        statement = statement.order_by(order_by)
    values = (await session.execute(statement)).scalars()
    return [_row(value, exclude=exclude) or {} for value in values]


def _report_summary(report: InvestigationReport | None) -> InvestigationReportSummary | None:
    if report is None:
        return None
    cause = report.incident_cause
    diagnosis = report.code_diagnosis
    facts = [
        value["text"]
        for value in report.confirmed_facts
        if isinstance(value, dict) and isinstance(value.get("text"), str)
    ]
    return InvestigationReportSummary(
        headline=report.headline,
        summary=report.summary,
        cause_status=str(cause.get("status", "not_found")),
        cause=str(cause.get("mechanism", "")),
        causal_chain=[str(item) for item in cause.get("causal_chain", [])],
        diagnosis_status=str(diagnosis.get("status", "not_found")),
        diagnosis=str(diagnosis.get("summary", "")),
        confirmed_facts=facts,
        evidence_gaps=[str(item) for item in report.evidence_gaps],
        next_step=report.next_step,
    )


def _audit_item(kind: AuditKind, row: Any) -> InvestigationAuditItem:
    if kind == "native_read_candidates":
        status, summary = "proposed", row.purpose
    elif kind == "access_decisions":
        status = "allowed" if row.outcome == "allow" else "rejected"
        summary = row.rejection_code or f"Validated by {row.parser_name}"
    elif kind == "authorized_reads":
        status, summary = "authorized", "Read authorization issued"
    elif kind == "read_attempts":
        status, summary = row.status, row.failure_code or f"Read attempt {row.attempt}"
    else:
        status = row.status
        summary = row.error_code or f"{row.role} model invocation"
    return InvestigationAuditItem(
        id=row.id,
        kind=kind,
        status=status,
        summary=summary,
        created_at=row.created_at,
    )


@router.post("", response_model=ManualInvestigationCreated, status_code=201)
async def create_manual_investigation(
    payload: ManualIncidentRequest,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ManualInvestigationCreated:
    user = await _require_workspace(
        session,
        user_id=user_id,
        workspace_id=payload.workspace_id,
        permission="analyze",
    )
    result = await PostgresIntakeStore(session).persist_manual(
        workspace_id=payload.workspace_id,
        incident=normalize_manual(payload),
        created_by=user.id,
    )
    session.add(
        AuditEvent(
            actor_id=user.id,
            actor_email=user.email,
            action="investigation.create.manual",
            target_type="investigation",
            target_id=result.investigation_public_id,
            workspace_id=payload.workspace_id,
            result="ok",
            detail={"source_type": "manual"},
        )
    )
    await session.commit()
    return ManualInvestigationCreated(
        id=result.investigation_public_id or "",
        workspace_id=payload.workspace_id,
        status="queued",
        job_id=result.job_id or 0,
    )


@router.get("", response_model=InvestigationListPage)
async def list_investigations(
    workspace_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None),
    result_state: str | None = Query(default=None),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    include_archived: bool = Query(default=False),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    allowed = await permitted_workspace_ids(session, user.id, user.role)
    statement = select(Investigation).where(Investigation.id > after_id)
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
                Investigation.public_id.ilike(pattern),
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
                public_id=investigation.public_id,
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
    investigation_id: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        public_id=investigation_id,
        permission="read",
    )
    internal_id = investigation.id
    input_row = await session.get(InvestigationInput, internal_id)
    report = await session.get(InvestigationReport, internal_id)
    operations = tuple(
        (
            await session.execute(
                select(InvestigationOperation)
                .where(InvestigationOperation.investigation_id == internal_id)
                .order_by(InvestigationOperation.ordinal)
            )
        ).scalars()
    )
    artifacts = tuple(
        (
            await session.execute(
                select(EvidenceArtifact)
                .where(EvidenceArtifact.investigation_id == internal_id)
                .order_by(EvidenceArtifact.id)
                .limit(100)
            )
        ).scalars()
    )
    error = input_row.error if input_row is not None else {}
    return InvestigationOverview(
        id=investigation.id,
        public_id=investigation.public_id,
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
        report=_report_summary(report),
        timeline=[
            InvestigationTimelineItem(
                ordinal=row.ordinal,
                kind=row.operation_kind,
                purpose=row.purpose,
                expected_evidence=row.expected_evidence,
                status=row.status,
                started_at=row.started_at,
                finished_at=row.finished_at,
                failure_code=row.failure_code,
            )
            for row in operations
        ],
        evidence=[
            EvidenceSummaryItem(
                id=row.id,
                kind=row.artifact_kind,
                evidence_class=row.evidence_class,
                data_class=row.data_class,
                source_revision=row.source_revision,
                source_time_start=row.source_time_start,
                source_time_end=row.source_time_end,
            )
            for row in artifacts
        ],
        operation_count=len(operations),
        evidence_count=int(
            await session.scalar(
                select(func.count()).select_from(EvidenceArtifact).where(
                    EvidenceArtifact.investigation_id == internal_id
                )
            )
            or 0
        ),
    )


@router.get("/{investigation_id}/technical")
async def get_investigation_technical(
    investigation_id: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, investigation = await _investigation_access(
        session,
        user_id=user_id,
        public_id=investigation_id,
        permission="read",
    )
    internal_id = investigation.id
    input_row = await session.get(InvestigationInput, internal_id)
    graph_snapshot = await session.get(InvestigationResourceGraphSnapshot, internal_id)
    policy_snapshot = await session.get(InvestigationModelPolicySnapshot, internal_id)
    report = await session.get(InvestigationReport, internal_id)
    return {
        "investigation": _row(investigation),
        "input": _row(input_row),
        "snapshot_summary": {
            "resource_graph": _row(graph_snapshot),
            "model_policy": _row(policy_snapshot),
            "repositories": await _rows(
                session,
                InvestigationRepositorySnapshot,
                internal_id,
                order_by=InvestigationRepositorySnapshot.id,
                exclude=frozenset({"credential_revision_id", "credential_identity_hash"}),
            ),
            "connectors": await _rows(
                session,
                InvestigationConnectorSnapshot,
                internal_id,
                order_by=InvestigationConnectorSnapshot.id,
                exclude=frozenset({"secret_ciphertext"}),
            ),
            "model_bindings": await _rows(
                session,
                InvestigationModelBindingSnapshot,
                internal_id,
                order_by=InvestigationModelBindingSnapshot.id,
            ),
        },
        "context_revisions": await _rows(
            session, ContextBundleRevision, internal_id, order_by=ContextBundleRevision.id
        ),
        "model_routing": await _rows(
            session, ModelRoutingDecision, internal_id, order_by=ModelRoutingDecision.id
        ),
        "steps": await _rows(
            session, InvestigationStep, internal_id, order_by=InvestigationStep.ordinal
        ),
        "decisions": await _rows(
            session, InvestigationDecision, internal_id, order_by=InvestigationDecision.ordinal
        ),
        "operations": await _rows(
            session, InvestigationOperation, internal_id, order_by=InvestigationOperation.ordinal
        ),
        "evidence": {
            "collections": await _rows(
                session, EvidenceCollection, internal_id, order_by=EvidenceCollection.id
            ),
            "artifacts": await _rows(
                session, EvidenceArtifact, internal_id, order_by=EvidenceArtifact.id
            ),
            "assertions": await _rows(
                session, EvidenceAssertion, internal_id, order_by=EvidenceAssertion.id
            ),
            "entities": await _rows(
                session, ObservedEntity, internal_id, order_by=ObservedEntity.id
            ),
            "events": await _rows(
                session, ObservedEvent, internal_id, order_by=ObservedEvent.occurred_at
            ),
            "relations": await _rows(
                session, ObservedRelation, internal_id, order_by=ObservedRelation.id
            ),
        },
        "source_revisions": await _rows(
            session, SourceRevision, internal_id, order_by=SourceRevision.id
        ),
        "source_assessments": await _rows(
            session, SourceAssessment, internal_id, order_by=SourceAssessment.id
        ),
        "code_findings": await _rows(
            session,
            InvestigationCodeFinding,
            internal_id,
            order_by=InvestigationCodeFinding.id,
        ),
        "report": _row(report),
    }


@router.get("/{investigation_id}/events")
async def get_investigation_events(
    investigation_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, investigation = await _investigation_access(
        session, user_id=user_id, public_id=investigation_id, permission="read"
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


@router.get("/{investigation_id}/audit", response_model=InvestigationAuditPage)
async def get_investigation_audit(
    investigation_id: str,
    kind: AuditKind = Query(default="access_decisions"),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, investigation = await _investigation_access(
        session, user_id=user_id, public_id=investigation_id, permission="read"
    )

    models: dict[AuditKind, Any] = {
        "native_read_candidates": NativeReadCandidate,
        "access_decisions": EvidenceAccessDecision,
        "authorized_reads": AuthorizedEvidenceRead,
        "read_attempts": EvidenceReadAttempt,
        "ai_invocations": AIInvocation,
    }
    model = models[kind]
    values = tuple(
        (
            await session.execute(
                select(model)
                .where(model.investigation_id == investigation.id, model.id > after_id)
                .order_by(model.id)
                .limit(limit + 1)
            )
        ).scalars()
    )
    page = values[:limit]
    return InvestigationAuditPage(
        items=[_audit_item(kind, row) for row in page],
        next_after_id=page[-1].id if len(values) > limit and page else None,
    )


@router.get("/{investigation_id}/stream")
async def stream_investigation(
    investigation_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user_id: int = Depends(require_user),
):
    async with AsyncSessionLocal() as access_session:
        _, investigation = await _investigation_access(
            access_session,
            user_id=user_id,
            public_id=investigation_id,
            permission="read",
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
                                "public_id": current.public_id,
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
    investigation_id: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, investigation = await _investigation_access(
        session, user_id=user_id, public_id=investigation_id, permission="analyze"
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
            actor_email=user.email,
            action="investigation.retry",
            target_type="investigation",
            target_id=result.investigation_public_id,
            workspace_id=investigation.workspace_id,
            result="ok",
            detail={"retry_of": investigation.public_id},
        )
    )
    await session.commit()
    return ManualInvestigationCreated(
        id=result.investigation_public_id or "",
        workspace_id=investigation.workspace_id,
        status="queued",
        job_id=result.job_id or 0,
    )


@router.post("/{investigation_id}/archive")
async def archive_investigation(
    investigation_id: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, investigation = await _investigation_access(
        session, user_id=user_id, public_id=investigation_id, permission="admin"
    )
    if investigation.status not in _TERMINAL_STATUSES:
        raise _error(
            409, "investigation_not_terminal", "Only a terminal investigation can archive."
        )
    if investigation.archived_at is not None:
        raise _error(409, "investigation_already_archived", "Investigation is already archived.")
    investigation.archived_at = datetime.now(UTC)
    investigation.archived_by = user.id
    session.add(
        AuditEvent(
            actor_id=user.id,
            actor_email=user.email,
            action="investigation.archive",
            target_type="investigation",
            target_id=investigation.public_id,
            workspace_id=investigation.workspace_id,
            result="ok",
            detail={},
        )
    )
    await session.commit()
    return {
        "id": investigation.public_id,
        "archived_at": investigation.archived_at,
        "archived_by": investigation.archived_by,
    }
