"""Incident, signal correlation, and human-owned action APIs."""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from lode.api.deps import assert_workspace_permission, permitted_workspace_ids, require_user
from lode.api.types import EntityId
from lode.application.incident_lifecycle import (
    IncidentLifecycleError,
    allowed_actions,
    transition_incident,
)
from lode.application.intake import ManualIncidentRequest, normalize_manual
from lode.db.models import (
    AuditEvent,
    EvidenceArtifact,
    Incident,
    IncidentAction,
    IncidentActionProposal,
    IncidentActionProposalDecision,
    IncidentEvent,
    IncidentKnowledgeCase,
    IncidentSignal,
    IncidentSignalLink,
    Investigation,
    InvestigationReview,
    User,
    WorkspacePermission,
)
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.intake_store import PostgresIntakeStore

router = APIRouter(prefix="/incidents", tags=["incidents"])
workspace_router = APIRouter(prefix="/workspaces", tags=["incidents"])


class IncidentActionCapabilityOut(BaseModel):
    action: str
    allowed: bool
    reason_code: str | None = None


class IncidentListItem(BaseModel):
    id: EntityId
    workspace_id: EntityId
    title: str
    severity: str
    state: str
    signal_count: int
    first_occurred_at: datetime
    last_occurred_at: datetime
    assigned_to: EntityId | None
    state_version: int
    recurrence_of_id: EntityId | None


class ManualIncidentCreated(BaseModel):
    outcome: str
    incident_id: EntityId
    signal_id: EntityId
    investigation_id: EntityId | None = None
    job_id: EntityId | None = None


class IncidentListPage(BaseModel):
    items: list[IncidentListItem]
    next_cursor: str | None


class IncidentSignalOut(BaseModel):
    id: EntityId
    schema_version: Literal["incident-signal.v1"]
    source_type: Literal["kafka", "manual"]
    source_event_id: str | None
    signal_kind: str
    observed_at: datetime
    severity: str
    title: str
    summary: str
    repository_binding_id: EntityId | None
    has_trace: bool
    source_revision: str | None
    fingerprint: str
    error_masked: dict


class InvestigationRunOut(BaseModel):
    id: EntityId
    status: str
    result_state: str
    trigger_reason: str
    trigger_signal_id: EntityId | None
    parent_investigation_id: EntityId | None
    created_at: datetime
    finished_at: datetime | None


class IncidentEventOut(BaseModel):
    id: EntityId
    event_type: str
    actor_id: EntityId | None
    payload: dict
    created_at: datetime


class IncidentActionOut(BaseModel):
    id: EntityId
    investigation_id: EntityId | None
    action_type: str
    status: str
    priority: str
    title: str
    rationale: str
    validation: str
    evidence_refs: list[EntityId]
    owner_id: EntityId | None
    created_by: EntityId | None
    state_version: int
    created_at: datetime
    updated_at: datetime


class IncidentAssigneeOut(BaseModel):
    user_id: EntityId
    username: str
    display_name: str
    status: str
    permission: str


class IncidentOverview(IncidentListItem):
    state_changed_at: datetime
    allowed_actions: list[IncidentActionCapabilityOut]
    signals: list[IncidentSignalOut]
    investigations: list[InvestigationRunOut]
    timeline: list[IncidentEventOut]
    actions: list[IncidentActionOut]


class IncidentTransitionRequest(BaseModel):
    expected_state_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2_000)


class IncidentSeverityRequest(BaseModel):
    severity: Literal["WARNING", "CRITICAL"]
    expected_state_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2_000)


class IncidentActionCreate(BaseModel):
    investigation_id: EntityId | None = None
    action_type: Literal["mitigate", "remediate", "validate", "prevent"]
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    title: str = Field(min_length=1, max_length=1_000)
    rationale: str = Field(min_length=1, max_length=20_000)
    validation: str = Field(min_length=1, max_length=20_000)
    evidence_refs: list[EntityId] = Field(default_factory=list, max_length=100)
    owner_id: EntityId


class IncidentActionUpdate(BaseModel):
    status: Literal["open", "in_progress", "blocked", "validation", "completed", "cancelled"]
    owner_id: EntityId
    expected_state_version: int = Field(gt=0)


class IncidentAssignmentRequest(BaseModel):
    owner_id: EntityId | None = Field(default=None, gt=0)
    expected_state_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2_000)


class ActionProposalOut(BaseModel):
    id: EntityId
    investigation_id: EntityId
    action_type: str
    priority: str
    title: str
    rationale: str
    validation: str
    evidence_refs: list[EntityId]
    decision: str | None
    action_id: EntityId | None
    created_at: datetime


class ActionProposalDecisionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4_000)
    owner_id: EntityId | None = None


class SimilarIncidentOut(BaseModel):
    incident_id: EntityId
    investigation_id: EntityId
    headline: str
    executive_summary: str
    causal_signature: list[str]
    similarity: float
    clue_only: Literal[True] = True


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def _error(status: int, code: str, message: str, **details: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **details})


async def _active_user(session: AsyncSession, user_id: EntityId) -> User:
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise _error(401, "active_user_required", "Active user required.")
    if user.must_change_password:
        raise _error(403, "password_change_required", "Password change required.")
    return user


async def _incident_access(
    session: AsyncSession,
    *,
    user_id: EntityId,
    incident_id: EntityId,
    permission: str,
) -> tuple[User, Incident]:
    user = await _active_user(session, user_id)
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise _error(404, "incident_not_found", "Incident not found.")
    await assert_workspace_permission(session, user, incident.workspace_id, permission)
    return user, incident


@workspace_router.post(
    "/{workspace_id}/manual-incidents", response_model=ManualIncidentCreated, status_code=201
)
async def create_manual_incident(
    workspace_id: EntityId,
    payload: ManualIncidentRequest,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=16, max_length=200
    ),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ManualIncidentCreated:
    user = await _active_user(session, user_id)
    await assert_workspace_permission(session, user, workspace_id, "operator")
    try:
        result = await PostgresIntakeStore(session).persist_manual(
            workspace_id=workspace_id,
            signal=normalize_manual(payload, idempotency_key=idempotency_key),
            created_by=user.id,
        )
    except ValueError as exc:
        await session.rollback()
        raise _error(422, "manual_incident_invalid", str(exc)) from exc
    if result.incident_id is None or result.signal_id is None:
        raise RuntimeError("manual incident persistence did not return signal ownership")
    session.add(
        AuditEvent(
            actor_id=user.id,
            actor_username=user.username,
            action="incident.create_manual",
            target_type="incident",
            target_id=str(result.incident_id),
            workspace_id=workspace_id,
            result="ok",
            detail={
                "source_type": "manual",
                "signal_id": result.signal_id,
                "investigation_id": result.investigation_id,
                "repository_binding_id": payload.repository_binding_id,
            },
        )
    )
    await session.commit()
    return ManualIncidentCreated(
        outcome=result.outcome,
        incident_id=result.incident_id,
        signal_id=result.signal_id,
        investigation_id=result.investigation_id,
        job_id=result.job_id,
    )


def _incident_item(row: Incident) -> IncidentListItem:
    return IncidentListItem(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        severity=row.severity,
        state=row.state,
        signal_count=row.signal_count,
        first_occurred_at=row.first_occurred_at,
        last_occurred_at=row.last_occurred_at,
        assigned_to=row.assigned_to,
        state_version=row.state_version,
        recurrence_of_id=row.recurrence_of_id,
    )


def _signal_out(row: IncidentSignal) -> IncidentSignalOut:
    return IncidentSignalOut(
        id=row.id,
        schema_version="incident-signal.v1",
        source_type=row.source_type,
        source_event_id=row.source_event_id,
        signal_kind=row.signal_kind,
        observed_at=row.observed_at,
        severity=row.severity,
        title=row.title,
        summary=row.summary,
        repository_binding_id=row.repository_binding_id,
        has_trace=row.trace_id_hash is not None,
        source_revision=row.source_revision,
        fingerprint=row.fingerprint,
        error_masked=row.error_masked,
    )


def _investigation_out(row: Investigation) -> InvestigationRunOut:
    return InvestigationRunOut(
        id=row.id,
        status=row.status,
        result_state=row.result_state,
        trigger_reason=row.trigger_reason,
        trigger_signal_id=row.trigger_signal_id,
        parent_investigation_id=row.parent_investigation_id,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _action_out(row: IncidentAction) -> IncidentActionOut:
    return IncidentActionOut(
        id=row.id,
        investigation_id=row.investigation_id,
        action_type=row.action_type,
        status=row.status,
        priority=row.priority,
        title=row.title,
        rationale=row.rationale,
        validation=row.validation,
        evidence_refs=[value for value in row.evidence_refs if isinstance(value, int) and value > 0],
        owner_id=row.owner_id,
        created_by=row.created_by,
        state_version=row.state_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _encode_cursor(row: Incident) -> str:
    raw = json.dumps(
        {"at": row.last_occurred_at.isoformat(), "id": row.id}, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw)
        at = datetime.fromisoformat(payload["at"])
        identifier = int(payload["id"])
        if at.tzinfo is None or identifier < 1:
            raise ValueError
        return at.astimezone(UTC), identifier
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error(422, "incident_cursor_invalid", "Incident cursor is invalid.") from exc


@router.get("", response_model=IncidentListPage)
async def list_incidents(
    workspace_id: EntityId | None = Query(default=None, gt=0),
    state: Literal["open", "acknowledged", "mitigated", "resolved", "closed"] | None = None,
    severity: Literal["UNCLASSIFIED", "WARNING", "CRITICAL"] | None = None,
    source_type: Literal["kafka", "manual"] | None = None,
    assigned_to: EntityId | None = Query(default=None, gt=0),
    report_state: Literal["confirmed", "hypothesis", "insufficient", "unavailable"] | None = None,
    observed_from: datetime | None = None,
    observed_to: datetime | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentListPage:
    user = await _active_user(session, user_id)
    allowed = await permitted_workspace_ids(session, user.id, user.is_system_admin)
    if allowed is not None and not allowed:
        return IncidentListPage(items=[], next_cursor=None)
    statement = select(Incident)
    if allowed is not None:
        statement = statement.where(Incident.workspace_id.in_(allowed))
    if workspace_id is not None:
        if allowed is not None and workspace_id not in allowed:
            raise _error(403, "workspace_read_forbidden", "Workspace read permission required.")
        statement = statement.where(Incident.workspace_id == workspace_id)
    if state is not None:
        statement = statement.where(Incident.state == state)
    if severity is not None:
        statement = statement.where(Incident.severity == severity)
    if assigned_to is not None:
        statement = statement.where(Incident.assigned_to == assigned_to)
    if source_type is not None:
        statement = statement.where(
            exists().where(
                IncidentSignalLink.incident_id == Incident.id,
                IncidentSignal.id == IncidentSignalLink.signal_id,
                IncidentSignal.source_type == source_type,
            )
        )
    if report_state is not None:
        statement = statement.where(
            exists().where(
                Investigation.incident_id == Incident.id,
                Investigation.result_state == report_state,
            )
        )
    if observed_from is not None:
        statement = statement.where(Incident.last_occurred_at >= observed_from)
    if observed_to is not None:
        statement = statement.where(Incident.last_occurred_at <= observed_to)
    if q is not None:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            Incident.title.ilike(pattern)
            | exists().where(
                IncidentSignalLink.incident_id == Incident.id,
                IncidentSignal.id == IncidentSignalLink.signal_id,
                or_(IncidentSignal.summary.ilike(pattern), IncidentSignal.title.ilike(pattern)),
            )
        )
    if cursor is not None:
        cursor_at, cursor_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                Incident.last_occurred_at < cursor_at,
                and_(Incident.last_occurred_at == cursor_at, Incident.id < cursor_id),
            )
        )
    rows = tuple(
        (
            await session.execute(
                statement.order_by(Incident.last_occurred_at.desc(), Incident.id.desc()).limit(
                    limit + 1
                )
            )
        ).scalars()
    )
    page = rows[:limit]
    return IncidentListPage(
        items=[_incident_item(row) for row in page],
        next_cursor=_encode_cursor(page[-1]) if len(rows) > limit and page else None,
    )


@router.get("/{incident_id}", response_model=IncidentOverview)
async def get_incident(
    incident_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="viewer"
    )
    signals = tuple(
        (
            await session.execute(
                select(IncidentSignal)
                .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                .where(IncidentSignalLink.incident_id == incident.id)
                .order_by(IncidentSignal.observed_at.desc(), IncidentSignal.id.desc())
            )
        ).scalars()
    )
    investigations = tuple(
        (
            await session.execute(
                select(Investigation)
                .where(Investigation.incident_id == incident.id)
                .order_by(Investigation.created_at.desc(), Investigation.id.desc())
            )
        ).scalars()
    )
    timeline = tuple(
        (
            await session.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident.id)
                .order_by(IncidentEvent.created_at.desc(), IncidentEvent.id.desc())
            )
        ).scalars()
    )
    actions = tuple(
        (
            await session.execute(
                select(IncidentAction)
                .where(IncidentAction.incident_id == incident.id)
                .order_by(IncidentAction.created_at.desc(), IncidentAction.id.desc())
            )
        ).scalars()
    )
    can_respond = user.is_system_admin
    if not can_respond:
        try:
            await assert_workspace_permission(session, user, incident.workspace_id, "operator")
            can_respond = True
        except HTTPException:
            can_respond = False
    item = _incident_item(incident)
    return IncidentOverview(
        **item.model_dump(),
        state_changed_at=incident.state_changed_at,
        allowed_actions=[
            IncidentActionCapabilityOut(
                action=value.action, allowed=value.allowed, reason_code=value.reason_code
            )
            for value in allowed_actions(state=incident.state, can_respond=can_respond)
        ],
        signals=[_signal_out(row) for row in signals],
        investigations=[_investigation_out(row) for row in investigations],
        timeline=[
            IncidentEventOut(
                id=row.id,
                event_type=row.event_type,
                actor_id=row.actor_id,
                payload=row.payload,
                created_at=row.created_at,
            )
            for row in timeline
        ],
        actions=[_action_out(row) for row in actions],
    )


def _similarity_tokens(value: str) -> set[str]:
    lowered = value.lower()
    words = set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]", lowered))
    compact = "".join(re.findall(r"[a-z0-9_\u4e00-\u9fff]", lowered))
    words.update(compact[index : index + 3] for index in range(max(0, len(compact) - 2)))
    return words


@router.get("/{incident_id}/similar-incidents", response_model=list[SimilarIncidentOut])
async def list_similar_incidents(
    incident_id: EntityId,
    limit: int = Query(default=10, ge=1, le=50),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[SimilarIncidentOut]:
    _, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="viewer"
    )
    summaries = tuple(
        (
            await session.execute(
                select(IncidentSignal.summary)
                .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                .where(IncidentSignalLink.incident_id == incident.id)
                .order_by(IncidentSignal.observed_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    query_tokens = _similarity_tokens("\n".join((incident.title, *summaries)))
    superseder = aliased(InvestigationReview)
    active_acceptance = exists(
        select(InvestigationReview.id).where(
            InvestigationReview.investigation_id
            == IncidentKnowledgeCase.investigation_id,
            InvestigationReview.code_finding_id.is_(None),
            InvestigationReview.verdict == "accepted",
            ~exists(
                select(superseder.id).where(
                    superseder.supersedes_review_id == InvestigationReview.id
                )
            ),
        )
    )
    cases = tuple(
        (
            await session.execute(
                select(IncidentKnowledgeCase)
                .where(
                    IncidentKnowledgeCase.workspace_id == incident.workspace_id,
                    IncidentKnowledgeCase.incident_id != incident.id,
                    active_acceptance,
                )
                .order_by(IncidentKnowledgeCase.created_at.desc())
                .limit(500)
            )
        ).scalars()
    )
    scored: list[tuple[float, IncidentKnowledgeCase]] = []
    for case in cases:
        candidate_tokens = _similarity_tokens(
            f"{case.search_document}\n{' '.join(case.causal_signature)}"
        )
        union = query_tokens | candidate_tokens
        score = len(query_tokens & candidate_tokens) / len(union) if union else 0.0
        if score > 0:
            scored.append((score, case))
    scored.sort(key=lambda item: (item[0], item[1].created_at, item[1].id), reverse=True)
    return [
        SimilarIncidentOut(
            incident_id=case.incident_id,
            investigation_id=case.investigation_id,
            headline=case.headline,
            executive_summary=case.executive_summary,
            causal_signature=case.causal_signature,
            similarity=round(score, 4),
        )
        for score, case in scored[:limit]
    ]


@router.get("/{incident_id}/assignees", response_model=list[IncidentAssigneeOut])
async def list_incident_assignees(
    incident_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[IncidentAssigneeOut]:
    _, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="viewer"
    )
    rows = (
        await session.execute(
            select(WorkspacePermission, User)
            .join(User, User.id == WorkspacePermission.user_id)
            .where(
                WorkspacePermission.workspace_id == incident.workspace_id,
                User.status == "active",
            )
            .order_by(User.display_name, User.id)
        )
    ).all()
    return [
        IncidentAssigneeOut(
            user_id=member.id,
            username=member.username,
            display_name=member.display_name,
            status=member.status,
            permission=grant.permission,
        )
        for grant, member in rows
    ]


async def _transition(
    *,
    incident_id: EntityId,
    command: Literal["acknowledge", "mitigate", "resolve", "close", "reopen"],
    payload: IncidentTransitionRequest,
    user_id: EntityId,
    session: AsyncSession,
) -> IncidentOverview:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    try:
        await transition_incident(
            session,
            incident=incident,
            command=command,
            actor_id=user.id,
            reason=payload.reason,
            expected_state_version=payload.expected_state_version,
        )
    except IncidentLifecycleError as exc:
        status = 409 if exc.code.endswith("conflict") or exc.code.endswith("invalid") else 422
        raise _error(status, exc.code, str(exc)) from exc
    await session.commit()
    await session.refresh(incident)
    return await get_incident(incident.id, user_id=user.id, session=session)


@router.post("/{incident_id}/acknowledge", response_model=IncidentOverview)
async def acknowledge_incident(
    incident_id: EntityId,
    payload: IncidentTransitionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    return await _transition(
        incident_id=incident_id,
        command="acknowledge",
        payload=payload,
        user_id=user_id,
        session=session,
    )


@router.post("/{incident_id}/mitigate", response_model=IncidentOverview)
async def mitigate_incident(
    incident_id: EntityId,
    payload: IncidentTransitionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    return await _transition(
        incident_id=incident_id,
        command="mitigate",
        payload=payload,
        user_id=user_id,
        session=session,
    )


@router.post("/{incident_id}/resolve", response_model=IncidentOverview)
async def resolve_incident(
    incident_id: EntityId,
    payload: IncidentTransitionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    return await _transition(
        incident_id=incident_id,
        command="resolve",
        payload=payload,
        user_id=user_id,
        session=session,
    )


@router.post("/{incident_id}/close", response_model=IncidentOverview)
async def close_incident(
    incident_id: EntityId,
    payload: IncidentTransitionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    return await _transition(
        incident_id=incident_id,
        command="close",
        payload=payload,
        user_id=user_id,
        session=session,
    )


@router.post("/{incident_id}/reopen", response_model=IncidentOverview)
async def reopen_incident(
    incident_id: EntityId,
    payload: IncidentTransitionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    return await _transition(
        incident_id=incident_id,
        command="reopen",
        payload=payload,
        user_id=user_id,
        session=session,
    )


@router.post("/{incident_id}/severity", response_model=IncidentOverview)
async def classify_incident_severity(
    incident_id: EntityId,
    payload: IncidentSeverityRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    if incident.state_version != payload.expected_state_version:
        raise _error(409, "incident_state_conflict", "Incident changed; reload before classifying.")
    previous = incident.severity
    incident.severity = payload.severity
    incident.state_version += 1
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            event_type="severity_changed",
            actor_id=user.id,
            payload={
                "from_severity": previous,
                "to_severity": payload.severity,
                "reason": payload.reason,
                "state_version": incident.state_version,
            },
        )
    )
    await session.commit()
    return await get_incident(incident.id, user_id=user.id, session=session)


@router.post("/{incident_id}/assign", response_model=IncidentOverview)
async def assign_incident(
    incident_id: EntityId,
    payload: IncidentAssignmentRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    if incident.state == "closed":
        raise _error(409, "incident_closed", "Closed incidents cannot be assigned.")
    if incident.state_version != payload.expected_state_version:
        raise _error(409, "incident_state_conflict", "Incident changed; reload it.")
    if payload.owner_id is not None:
        owner = await _active_user(session, payload.owner_id)
        await assert_workspace_permission(session, owner, incident.workspace_id, "viewer")
    previous_owner_id = incident.assigned_to
    incident.assigned_to = payload.owner_id
    incident.state_version += 1
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            event_type="assigned",
            actor_id=user.id,
            payload={
                "from_owner_id": previous_owner_id,
                "to_owner_id": payload.owner_id,
                "reason": payload.reason,
                "state_version": incident.state_version,
            },
        )
    )
    await session.commit()
    return await get_incident(incident.id, user_id=user.id, session=session)


@router.post("/{incident_id}/investigations", response_model=InvestigationRunOut, status_code=201)
async def start_incident_investigation(
    incident_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> InvestigationRunOut:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    if incident.state == "closed":
        raise _error(409, "incident_closed", "Closed incidents cannot start an investigation.")
    result = await PostgresIntakeStore(session).start_investigation_for_incident(
        incident_id=incident.id, created_by=user.id
    )
    await session.commit()
    investigation = await session.get(Investigation, result.investigation_id)
    assert investigation is not None
    return _investigation_out(investigation)


@router.post(
    "/{incident_id}/investigations/{investigation_id}/retry",
    response_model=InvestigationRunOut,
    status_code=201,
)
async def retry_incident_investigation(
    incident_id: EntityId,
    investigation_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> InvestigationRunOut:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    if incident.state == "closed":
        raise _error(409, "incident_closed", "Closed incidents cannot retry an investigation.")
    previous = await session.get(Investigation, investigation_id)
    if previous is None or previous.incident_id != incident.id:
        raise _error(404, "investigation_not_found", "Investigation not found for incident.")
    try:
        result = await PostgresIntakeStore(session).retry_investigation(
            investigation_id=previous.id, created_by=user.id
        )
    except ValueError as exc:
        raise _error(409, "investigation_retry_invalid", str(exc)) from exc
    await session.commit()
    retried = await session.get(Investigation, result.investigation_id)
    assert retried is not None
    return _investigation_out(retried)


async def _validate_action_owner(
    session: AsyncSession, incident: Incident, owner_id: int | None
) -> None:
    if owner_id is None:
        return
    owner = await _active_user(session, owner_id)
    await assert_workspace_permission(session, owner, incident.workspace_id, "viewer")


async def _validate_action_evidence(
    session: AsyncSession,
    incident: Incident,
    investigation_id: int | None,
    evidence_refs: list[int],
) -> None:
    if investigation_id is None:
        if evidence_refs:
            raise _error(422, "action_evidence_requires_run", "Evidence requires an investigation.")
        return
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None or investigation.incident_id != incident.id:
        raise _error(422, "investigation_incident_mismatch", "Investigation is not in incident.")
    if not evidence_refs:
        return
    found = set(
        (
            await session.execute(
                select(EvidenceArtifact.id).where(
                    EvidenceArtifact.investigation_id == investigation.id,
                    EvidenceArtifact.id.in_(set(evidence_refs)),
                )
            )
        ).scalars()
    )
    if found != set(evidence_refs):
        raise _error(422, "action_evidence_invalid", "Evidence is not owned by the investigation.")


def _proposal_out(
    proposal: IncidentActionProposal,
    decision: IncidentActionProposalDecision | None,
) -> ActionProposalOut:
    return ActionProposalOut(
        id=proposal.id,
        investigation_id=proposal.investigation_id,
        action_type=proposal.action_type,
        priority=proposal.priority,
        title=proposal.title,
        rationale=proposal.rationale,
        validation=proposal.validation,
        evidence_refs=proposal.evidence_refs,
        decision=None if decision is None else decision.decision,
        action_id=None if decision is None else decision.action_id,
        created_at=proposal.created_at,
    )


@router.get("/{incident_id}/action-proposals", response_model=list[ActionProposalOut])
async def list_action_proposals(
    incident_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[ActionProposalOut]:
    _, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="viewer"
    )
    rows = (
        await session.execute(
            select(IncidentActionProposal, IncidentActionProposalDecision)
            .outerjoin(
                IncidentActionProposalDecision,
                IncidentActionProposalDecision.proposal_id == IncidentActionProposal.id,
            )
            .where(IncidentActionProposal.incident_id == incident.id)
            .order_by(IncidentActionProposal.created_at.desc(), IncidentActionProposal.id.desc())
        )
    ).all()
    return [_proposal_out(proposal, decision) for proposal, decision in rows]


async def _decide_action_proposal(
    *,
    incident_id: EntityId,
    proposal_id: EntityId,
    decision_value: Literal["accepted", "rejected"],
    payload: ActionProposalDecisionRequest,
    user_id: EntityId,
    session: AsyncSession,
) -> ActionProposalOut:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    proposal = await session.get(IncidentActionProposal, proposal_id, with_for_update=True)
    if proposal is None or proposal.incident_id != incident.id:
        raise _error(404, "action_proposal_not_found", "Action proposal not found.")
    existing = await session.scalar(
        select(IncidentActionProposalDecision.id).where(
            IncidentActionProposalDecision.proposal_id == proposal.id
        )
    )
    if existing is not None:
        raise _error(409, "action_proposal_already_decided", "Action proposal was decided.")
    action: IncidentAction | None = None
    if decision_value == "accepted":
        if payload.owner_id is None:
            raise _error(422, "action_owner_required", "Accepted actions require an owner.")
        await _validate_action_owner(session, incident, payload.owner_id)
        await _validate_action_evidence(
            session, incident, proposal.investigation_id, list(proposal.evidence_refs)
        )
        action = IncidentAction(
            incident_id=incident.id,
            investigation_id=proposal.investigation_id,
            action_type=proposal.action_type,
            priority=proposal.priority,
            title=proposal.title,
            rationale=proposal.rationale,
            validation=proposal.validation,
            evidence_refs=list(proposal.evidence_refs),
            owner_id=payload.owner_id,
            created_by=user.id,
        )
        session.add(action)
        await session.flush()
        session.add(
            IncidentEvent(
                incident_id=incident.id,
                event_type="action_created",
                actor_id=user.id,
                payload={"action_id": action.id, "proposal_id": proposal.id},
            )
        )
    decision = IncidentActionProposalDecision(
        proposal_id=proposal.id,
        decision=decision_value,
        actor_id=user.id,
        reason=payload.reason,
        action_id=None if action is None else action.id,
    )
    session.add(decision)
    await session.commit()
    await session.refresh(decision)
    return _proposal_out(proposal, decision)


@router.post(
    "/{incident_id}/action-proposals/{proposal_id}/accept",
    response_model=ActionProposalOut,
)
async def accept_action_proposal(
    incident_id: EntityId,
    proposal_id: EntityId,
    payload: ActionProposalDecisionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ActionProposalOut:
    return await _decide_action_proposal(
        incident_id=incident_id,
        proposal_id=proposal_id,
        decision_value="accepted",
        payload=payload,
        user_id=user_id,
        session=session,
    )


@router.post(
    "/{incident_id}/action-proposals/{proposal_id}/reject",
    response_model=ActionProposalOut,
)
async def reject_action_proposal(
    incident_id: EntityId,
    proposal_id: EntityId,
    payload: ActionProposalDecisionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ActionProposalOut:
    return await _decide_action_proposal(
        incident_id=incident_id,
        proposal_id=proposal_id,
        decision_value="rejected",
        payload=payload,
        user_id=user_id,
        session=session,
    )


@router.post("/{incident_id}/actions", response_model=IncidentActionOut, status_code=201)
async def create_incident_action(
    incident_id: EntityId,
    payload: IncidentActionCreate,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentActionOut:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    if incident.state == "closed":
        raise _error(409, "incident_closed", "Closed incidents cannot create actions.")
    await _validate_action_owner(session, incident, payload.owner_id)
    await _validate_action_evidence(
        session, incident, payload.investigation_id, list(payload.evidence_refs)
    )
    action = IncidentAction(
        incident_id=incident.id,
        investigation_id=payload.investigation_id,
        action_type=payload.action_type,
        priority=payload.priority,
        title=payload.title,
        rationale=payload.rationale,
        validation=payload.validation,
        evidence_refs=list(payload.evidence_refs),
        owner_id=payload.owner_id,
        created_by=user.id,
    )
    session.add(action)
    await session.flush()
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            event_type="action_created",
            actor_id=user.id,
            payload={"action_id": action.id, "action_type": action.action_type},
        )
    )
    await session.commit()
    await session.refresh(action)
    return _action_out(action)


_ACTION_TRANSITIONS = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"blocked", "validation", "cancelled"},
    "blocked": {"in_progress", "cancelled"},
    "validation": {"in_progress", "completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


@router.patch("/{incident_id}/actions/{action_id}", response_model=IncidentActionOut)
async def update_incident_action(
    incident_id: EntityId,
    action_id: EntityId,
    payload: IncidentActionUpdate,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentActionOut:
    user, incident = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    action = await session.get(IncidentAction, action_id, with_for_update=True)
    if action is None or action.incident_id != incident.id:
        raise _error(404, "incident_action_not_found", "Incident action not found.")
    if action.state_version != payload.expected_state_version:
        raise _error(409, "incident_action_conflict", "Action changed; reload it.")
    if payload.status != action.status and payload.status not in _ACTION_TRANSITIONS[action.status]:
        raise _error(409, "incident_action_transition_invalid", "Action transition is invalid.")
    await _validate_action_owner(session, incident, payload.owner_id)
    previous_status = action.status
    action.status = payload.status
    action.owner_id = payload.owner_id
    action.state_version += 1
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            event_type="action_updated",
            actor_id=user.id,
            payload={
                "action_id": action.id,
                "from_status": previous_status,
                "to_status": action.status,
                "state_version": action.state_version,
            },
        )
    )
    await session.commit()
    await session.refresh(action)
    return _action_out(action)
