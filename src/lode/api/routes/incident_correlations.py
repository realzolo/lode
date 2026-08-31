"""Deterministic correlation review, incident merge, and incident split APIs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import assert_workspace_permission, require_user
from lode.api.routes.incidents import (
    IncidentOverview,
    _active_user,
    _error,
    _incident_access,
    get_incident,
    get_session,
)
from lode.api.types import EntityId
from lode.db.models import (
    Incident,
    IncidentCorrelationCandidate,
    IncidentCorrelationDecision,
    IncidentEvent,
    IncidentSignal,
    IncidentSignalAssociationEvent,
    IncidentSignalLink,
)

router = APIRouter(prefix="/incidents", tags=["incident-correlation"])
workspace_router = APIRouter(prefix="/workspaces", tags=["incident-correlation"])


class CorrelationCandidateOut(BaseModel):
    id: EntityId
    signal_id: EntityId
    current_incident_id: EntityId
    candidate_incident_id: EntityId
    score: float
    factors: dict
    status: str
    created_at: datetime


class CorrelationDecisionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class IncidentMergeRequest(BaseModel):
    source_incident_id: EntityId
    reason: str = Field(min_length=1, max_length=2_000)


class IncidentSplitRequest(BaseModel):
    signal_ids: list[EntityId] = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=2_000)
    reason: str = Field(min_length=1, max_length=2_000)


@workspace_router.get(
    "/{workspace_id}/correlation-candidates", response_model=list[CorrelationCandidateOut]
)
async def list_correlation_candidates(
    workspace_id: EntityId,
    status: Literal["pending", "accepted", "rejected"] = "pending",
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[CorrelationCandidateOut]:
    user = await _active_user(session, user_id)
    await assert_workspace_permission(session, user, workspace_id, "viewer")
    rows = (
        await session.execute(
            select(IncidentCorrelationCandidate, IncidentSignalLink)
            .join(IncidentSignal, IncidentSignal.id == IncidentCorrelationCandidate.signal_id)
            .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
            .where(
                IncidentCorrelationCandidate.workspace_id == workspace_id,
                IncidentCorrelationCandidate.status == status,
            )
            .order_by(IncidentCorrelationCandidate.created_at.desc())
        )
    ).all()
    return [
        CorrelationCandidateOut(
            id=candidate.id,
            signal_id=candidate.signal_id,
            current_incident_id=link.incident_id,
            candidate_incident_id=candidate.candidate_incident_id,
            score=float(candidate.score),
            factors=candidate.factors,
            status=candidate.status,
            created_at=candidate.created_at,
        )
        for candidate, link in rows
    ]


async def _recalculate_incident_projection(session: AsyncSession, incident: Incident) -> None:
    count, first, last = (
        await session.execute(
            select(
                func.count(IncidentSignal.id),
                func.min(IncidentSignal.observed_at),
                func.max(IncidentSignal.observed_at),
            )
            .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
            .where(IncidentSignalLink.incident_id == incident.id)
        )
    ).one()
    incident.signal_count = int(count)
    if first is not None and last is not None:
        incident.first_occurred_at = first
        incident.last_occurred_at = last


@router.post("/correlation-candidates/{candidate_id}/accept", response_model=IncidentOverview)
async def accept_correlation_candidate(
    candidate_id: EntityId,
    payload: CorrelationDecisionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    user = await _active_user(session, user_id)
    candidate = await session.get(IncidentCorrelationCandidate, candidate_id, with_for_update=True)
    if candidate is None:
        raise _error(404, "correlation_candidate_not_found", "Candidate not found.")
    await assert_workspace_permission(session, user, candidate.workspace_id, "operator")
    if candidate.status != "pending":
        raise _error(409, "correlation_candidate_decided", "Candidate was already decided.")
    signal = await session.get(IncidentSignal, candidate.signal_id)
    link = await session.get(IncidentSignalLink, candidate.signal_id, with_for_update=True)
    target = await session.get(Incident, candidate.candidate_incident_id, with_for_update=True)
    source = await session.get(Incident, link.incident_id, with_for_update=True) if link else None
    if signal is None or link is None or source is None or target is None:
        raise _error(409, "correlation_candidate_stale", "Candidate ownership is unavailable.")
    decision = IncidentCorrelationDecision(
        workspace_id=candidate.workspace_id,
        signal_id=signal.id,
        incident_id=target.id,
        outcome="operator_linked",
        score=candidate.score,
        factors={**candidate.factors, "operator_reason": payload.reason},
    )
    session.add(decision)
    await session.flush()
    session.add_all(
        [
            IncidentSignalAssociationEvent(
                signal_id=signal.id,
                incident_id=source.id,
                correlation_decision_id=decision.id,
                event_type="unlinked",
                actor_id=user.id,
                reason=payload.reason,
            ),
            IncidentSignalAssociationEvent(
                signal_id=signal.id,
                incident_id=target.id,
                correlation_decision_id=decision.id,
                event_type="linked",
                actor_id=user.id,
                reason=payload.reason,
            ),
        ]
    )
    link.incident_id = target.id
    link.state_version += 1
    candidate.status = "accepted"
    candidate.decided_by = user.id
    candidate.decided_at = datetime.now(UTC)
    await _recalculate_incident_projection(session, source)
    await _recalculate_incident_projection(session, target)
    if source.signal_count == 0:
        source.state = "closed"
        source.state_changed_at = datetime.now(UTC)
        source.state_version += 1
    await session.commit()
    return await get_incident(target.id, user_id=user.id, session=session)


@router.post(
    "/correlation-candidates/{candidate_id}/reject", response_model=CorrelationCandidateOut
)
async def reject_correlation_candidate(
    candidate_id: EntityId,
    payload: CorrelationDecisionRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> CorrelationCandidateOut:
    user = await _active_user(session, user_id)
    candidate = await session.get(IncidentCorrelationCandidate, candidate_id, with_for_update=True)
    if candidate is None:
        raise _error(404, "correlation_candidate_not_found", "Candidate not found.")
    await assert_workspace_permission(session, user, candidate.workspace_id, "operator")
    if candidate.status != "pending":
        raise _error(409, "correlation_candidate_decided", "Candidate was already decided.")
    link = await session.get(IncidentSignalLink, candidate.signal_id)
    if link is None:
        raise _error(409, "correlation_candidate_stale", "Candidate ownership is unavailable.")
    candidate.status = "rejected"
    candidate.decided_by = user.id
    candidate.decided_at = datetime.now(UTC)
    session.add(
        IncidentEvent(
            incident_id=link.incident_id,
            event_type="signal_unlinked",
            actor_id=user.id,
            payload={"candidate_id": candidate.id, "reason": payload.reason},
        )
    )
    await session.commit()
    return CorrelationCandidateOut(
        id=candidate.id,
        signal_id=candidate.signal_id,
        current_incident_id=link.incident_id,
        candidate_incident_id=candidate.candidate_incident_id,
        score=float(candidate.score),
        factors=candidate.factors,
        status=candidate.status,
        created_at=candidate.created_at,
    )


@router.post("/{incident_id}/merge", response_model=IncidentOverview)
async def merge_incident(
    incident_id: EntityId,
    payload: IncidentMergeRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    user, target = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    source = await session.get(Incident, payload.source_incident_id, with_for_update=True)
    if source is None or source.workspace_id != target.workspace_id or source.id == target.id:
        raise _error(422, "incident_merge_invalid", "Source incident is invalid.")
    signal_rows = tuple(
        (
            await session.execute(
                select(IncidentSignal, IncidentSignalLink)
                .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                .where(IncidentSignalLink.incident_id == source.id)
                .with_for_update(of=IncidentSignalLink)
            )
        ).all()
    )
    for signal, link in signal_rows:
        session.add(
            IncidentSignalAssociationEvent(
                signal_id=signal.id,
                incident_id=source.id,
                event_type="unlinked",
                actor_id=user.id,
                reason=payload.reason,
            )
        )
        link.incident_id = target.id
        link.state_version += 1
        session.add(
            IncidentSignalAssociationEvent(
                signal_id=signal.id,
                incident_id=target.id,
                event_type="linked",
                actor_id=user.id,
                reason=payload.reason,
            )
        )
    await _recalculate_incident_projection(session, source)
    await _recalculate_incident_projection(session, target)
    source.state = "closed"
    source.state_changed_at = datetime.now(UTC)
    source.state_version += 1
    session.add(
        IncidentEvent(
            incident_id=target.id,
            event_type="incidents_merged",
            actor_id=user.id,
            payload={"source_incident_id": source.id, "reason": payload.reason},
        )
    )
    await session.commit()
    return await get_incident(target.id, user_id=user.id, session=session)


@router.post("/{incident_id}/split", response_model=IncidentOverview, status_code=201)
async def split_incident(
    incident_id: EntityId,
    payload: IncidentSplitRequest,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> IncidentOverview:
    user, source = await _incident_access(
        session, user_id=user_id, incident_id=incident_id, permission="operator"
    )
    signal_rows = tuple(
        (
            await session.execute(
                select(IncidentSignal, IncidentSignalLink)
                .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                .where(
                    IncidentSignalLink.incident_id == source.id,
                    IncidentSignal.id.in_(set(payload.signal_ids)),
                )
                .with_for_update(of=IncidentSignalLink)
            )
        ).all()
    )
    rows = tuple(signal for signal, _link in signal_rows)
    if len(rows) != len(set(payload.signal_ids)) or len(rows) >= source.signal_count:
        raise _error(422, "incident_split_invalid", "Split must select a strict signal subset.")
    first = min(row.observed_at for row in rows)
    last = max(row.observed_at for row in rows)
    severity = "CRITICAL" if any(row.severity == "CRITICAL" for row in rows) else rows[0].severity
    target = Incident(
        workspace_id=source.workspace_id,
        title=payload.title,
        severity=severity,
        state="open",
        first_occurred_at=first,
        last_occurred_at=last,
        signal_count=len(rows),
        state_changed_at=datetime.now(UTC),
    )
    session.add(target)
    await session.flush()
    for signal, link in signal_rows:
        session.add(
            IncidentSignalAssociationEvent(
                signal_id=signal.id,
                incident_id=source.id,
                event_type="unlinked",
                actor_id=user.id,
                reason=payload.reason,
            )
        )
        link.incident_id = target.id
        link.state_version += 1
        session.add(
            IncidentSignalAssociationEvent(
                signal_id=signal.id,
                incident_id=target.id,
                event_type="linked",
                actor_id=user.id,
                reason=payload.reason,
            )
        )
    await _recalculate_incident_projection(session, source)
    session.add(
        IncidentEvent(
            incident_id=source.id,
            event_type="incident_split",
            actor_id=user.id,
            payload={
                "new_incident_id": target.id,
                "signal_ids": payload.signal_ids,
                "reason": payload.reason,
            },
        )
    )
    await session.commit()
    return await get_incident(target.id, user_id=user.id, session=session)
