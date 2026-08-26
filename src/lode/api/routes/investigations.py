"""Manual investigation intake endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import require_user
from lode.application.intake import ManualIncidentRequest, normalize_manual
from lode.db.models import AuditEvent, User, Workspace, WorkspacePermission
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.intake_store import PostgresIntakeStore

router = APIRouter(prefix="/investigations", tags=["investigations"])


class ManualInvestigationCreated(BaseModel):
    id: str
    workspace_id: int
    status: str
    job_id: int


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _require_analyze(
    session: AsyncSession, *, user_id: int, workspace_id: int
) -> User:
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="active user required")
    if await session.get(Workspace, workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if user.role == "admin":
        return user
    permission = await session.scalar(
        select(WorkspacePermission.permission).where(
            WorkspacePermission.workspace_id == workspace_id,
            WorkspacePermission.user_id == user_id,
        )
    )
    if permission not in {"analyze", "admin"}:
        raise HTTPException(status_code=403, detail="Workspace analyze permission required")
    return user


@router.post("", response_model=ManualInvestigationCreated, status_code=201)
async def create_manual_investigation(
    payload: ManualIncidentRequest,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ManualInvestigationCreated:
    user = await _require_analyze(
        session, user_id=user_id, workspace_id=payload.workspace_id
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
