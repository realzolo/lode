"""Incident-first operational API with server-driven action capabilities."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    Incident,
    IncidentAction,
    IncidentEvent,
    IncidentOccurrence,
    Investigation,
    User,
    WorkspacePermission,
)
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.intake_store import IncidentCorrelationError, PostgresIntakeStore

router = APIRouter(prefix="/incidents", tags=["incidents"])


class IncidentActionCapabilityOut(BaseModel):
    action: str
    allowed: bool
    reason_code: str | None = None


class IncidentListItem(BaseModel):
    id: EntityId
    workspace_id: EntityId
    dedup_key: str
    event: str
    component: str | None
    environment: str | None
    severity: str
    state: str
    occurrence_count: int
    first_occurred_at: datetime
    last_occurred_at: datetime
    assigned_to: EntityId | None
    state_version: int
    recurrence_of_id: EntityId | None


class ManualIncidentCreated(BaseModel):
    incident_id: EntityId
    investigation_id: EntityId | None = None
    job_id: EntityId | None = None


class IncidentListPage(BaseModel):
    items: list[IncidentListItem]
    next_after_id: EntityId | None


class IncidentOccurrenceOut(BaseModel):
    id: EntityId
    source_type: str
    source_event_id: str | None
    event_kind: str
    occurred_at: datetime
    severity: str
    event: str
    component: str | None
    environment: str | None
    source_revision: str | None


class InvestigationRunOut(BaseModel):
    id: EntityId
    status: str
    result_state: str
    trigger_reason: str
    trigger_occurrence_id: EntityId | None
    retry_of_id: EntityId | None
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
    occurrences: list[IncidentOccurrenceOut]
    investigations: list[InvestigationRunOut]
    timeline: list[IncidentEventOut]
    actions: list[IncidentActionOut]


class IncidentTransitionRequest(BaseModel):
    expected_state_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)


class IncidentActionCreate(BaseModel):
    investigation_id: EntityId | None = None
    action_type: Literal["mitigate", "remediate", "validate", "prevent"]
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    title: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=20_000)
    validation: str = Field(min_length=1, max_length=20_000)
    evidence_refs: list[EntityId] = Field(default_factory=list, max_length=100)
    owner_id: EntityId | None = None


class IncidentActionUpdate(BaseModel):
    status: Literal["accepted", "in_progress", "verified", "rejected", "cancelled"]
    owner_id: EntityId | None = None


class IncidentAssignmentRequest(BaseModel):
    owner_id: EntityId | None = Field(default=None, gt=0)
    expected_state_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)


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


@router.post("", response_model=ManualIncidentCreated, status_code=201)
async def create_manual_incident(
    payload: ManualIncidentRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ManualIncidentCreated:
    user = await _active_user(session, user_id)
    await assert_workspace_permission(session, user, payload.workspace_id, "operator")
    try:
        result = await PostgresIntakeStore(session).persist_manual(
            workspace_id=payload.workspace_id,
            incident=normalize_manual(payload),
            created_by=user.id,
        )
    except IncidentCorrelationError as exc:
        await session.rollback()
        raise _error(409, "incident_correlation_failed", str(exc)) from exc
    if result.incident_id is None:
        raise RuntimeError("manual incident persistence did not return an incident")
    session.add(
        AuditEvent(
            actor_id=user.id,
            actor_username=user.username,
            action="incident.create_manual",
            target_type="incident",
            target_id=str(result.incident_id),
            workspace_id=payload.workspace_id,
            result="ok",
            detail={"source_type": "manual", "investigation_id": result.investigation_id},
        )
    )
    await session.commit()
    return ManualIncidentCreated(
        incident_id=result.incident_id,
        investigation_id=result.investigation_id,
        job_id=result.job_id,
    )


def _incident_item(row: Incident) -> IncidentListItem:
    return IncidentListItem(
        id=row.id,
        workspace_id=row.workspace_id,
        dedup_key=row.dedup_key,
        event=row.event,
        component=row.component,
        environment=row.environment,
        severity=row.severity,
        state=row.state,
        occurrence_count=row.occurrence_count,
        first_occurred_at=row.first_occurred_at,
        last_occurred_at=row.last_occurred_at,
        assigned_to=row.assigned_to,
        state_version=row.state_version,
        recurrence_of_id=row.recurrence_of_id,
    )


def _occurrence_out(row: IncidentOccurrence) -> IncidentOccurrenceOut:
    return IncidentOccurrenceOut(
        id=row.id,
        source_type=row.source_type,
        source_event_id=row.source_event_id,
        event_kind=row.event_kind,
        occurred_at=row.occurred_at,
        severity=row.severity,
        event=row.event,
        component=row.component,
        environment=row.environment,
        source_revision=row.source_revision,
    )


def _investigation_out(row: Investigation) -> InvestigationRunOut:
    return InvestigationRunOut(
        id=row.id,
        status=row.status,
        result_state=row.result_state,
        trigger_reason=row.trigger_reason,
        trigger_occurrence_id=row.trigger_occurrence_id,
        retry_of_id=row.retry_of_id,
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
        evidence_refs=[
            value for value in row.evidence_refs if isinstance(value, int) and value > 0
        ],
        owner_id=row.owner_id,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=IncidentListPage)
async def list_incidents(
    workspace_id: EntityId | None = Query(default=None, gt=0),
    state: Literal["open", "acknowledged", "mitigated", "resolved", "closed"] | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    after_id: EntityId | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentListPage:
    user = await _active_user(session, user_id)
    allowed = await permitted_workspace_ids(session, user.id, user.is_system_admin)
    if allowed is not None and not allowed:
        return IncidentListPage(items=[], next_after_id=None)
    statement = select(Incident)
    if allowed is not None:
        statement = statement.where(Incident.workspace_id.in_(allowed))
    if workspace_id is not None:
        if allowed is not None and workspace_id not in allowed:
            raise _error(403, "workspace_read_forbidden", "Workspace read permission required.")
        statement = statement.where(Incident.workspace_id == workspace_id)
    if state is not None:
        statement = statement.where(Incident.state == state)
    if q is not None:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            Incident.event.ilike(pattern)
            | Incident.component.ilike(pattern)
            | Incident.dedup_key.ilike(pattern)
        )
    if after_id is not None:
        statement = statement.where(Incident.id > after_id)
    rows = tuple(
        (await session.execute(statement.order_by(Incident.id).limit(limit + 1))).scalars()
    )
    page = rows[:limit]
    return IncidentListPage(
        items=[_incident_item(row) for row in page],
        next_after_id=page[-1].id if len(rows) > limit and page else None,
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

    occurrences = tuple(
        (
            await session.execute(
                select(IncidentOccurrence)
                .where(IncidentOccurrence.incident_id == incident.id)
                .order_by(IncidentOccurrence.occurred_at.desc(), IncidentOccurrence.id.desc())
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
        occurrences=[_occurrence_out(row) for row in occurrences],
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
        raise _error(
            409,
            "incident_state_conflict",
            "Incident changed; reload it before applying another action.",
        )
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
    await session.refresh(incident)
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
    if payload.investigation_id is not None:
        investigation = await session.get(Investigation, payload.investigation_id)
        if investigation is None or investigation.incident_id != incident.id:
            raise _error(
                422, "investigation_incident_mismatch", "Investigation does not belong to incident."
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
    action = await session.get(IncidentAction, action_id)
    if action is None or action.incident_id != incident.id:
        raise _error(404, "incident_action_not_found", "Incident action not found.")
    previous_status = action.status
    action.status = payload.status
    action.owner_id = payload.owner_id
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            event_type="action_updated",
            actor_id=user.id,
            payload={
                "action_id": action.id,
                "from_status": previous_status,
                "to_status": action.status,
            },
        )
    )
    await session.commit()
    await session.refresh(action)
    return _action_out(action)
