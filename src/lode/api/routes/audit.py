"""Audit log read API — admin only.

Surfaces the append-only ``audit_events`` table so operators can actually *read*
the trail that ``audit_action`` writes. Every privileged control-plane mutation
records an event there; this endpoint makes that trail observable. It is
read-only and gated by ``require_admin`` (which itself requires a valid token),
so the audit log is never exposed to non-admins.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import require_admin
from lode.api.schemas import AuditEventListOut, AuditEventOut
from lode.db.models.intake import AuditEvent
from lode.db.session import AsyncSessionLocal

logger = logging.getLogger("lode.api.audit_routes")

router = APIRouter(prefix="/audit", tags=["audit"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def _utc_aware(dt: datetime) -> datetime:
    """Coerce a naive query timestamp to UTC so it can be compared against the
    timezone-aware ``created_at`` column without a comparison error."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@router.get("", response_model=AuditEventListOut)
async def list_audit_events(
    action: str | None = Query(
        default=None, description="Exact action, e.g. application.create"
    ),
    actor_id: int | None = Query(default=None),
    actor_email: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    application_id: int | None = Query(default=None),
    result: str | None = Query(default=None, pattern="^(ok|error)$"),
    since: datetime | None = Query(
        default=None, description="ISO timestamp; only events at/after this time"
    ),
    until: datetime | None = Query(
        default=None, description="ISO timestamp; only events before this time"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AuditEventListOut:
    filters = []
    if action is not None:
        filters.append(AuditEvent.action == action)
    if actor_id is not None:
        filters.append(AuditEvent.actor_id == actor_id)
    if actor_email is not None:
        filters.append(AuditEvent.actor_email == actor_email)
    if target_type is not None:
        filters.append(AuditEvent.target_type == target_type)
    if target_id is not None:
        filters.append(AuditEvent.target_id == target_id)
    if application_id is not None:
        filters.append(AuditEvent.application_id == application_id)
    if result is not None:
        filters.append(AuditEvent.result == result)
    if since is not None:
        filters.append(AuditEvent.created_at >= _utc_aware(since))
    if until is not None:
        filters.append(AuditEvent.created_at < _utc_aware(until))

    total = (
        await session.execute(
            select(func.count()).select_from(AuditEvent).where(*filters)
        )
    ).scalar_one()

    stmt = (
        select(AuditEvent)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if filters:
        stmt = stmt.where(*filters)
    rows = (await session.execute(stmt)).scalars().all()

    return AuditEventListOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[AuditEventOut.model_validate(r) for r in rows],
    )
