"""Shared experience routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import permitted_app_ids, require_user
from lode.api.schemas import ExperienceOut
from lode.db.models.application import Application
from lode.db.models.experience import Experience
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal

router = APIRouter(prefix="/experiences", tags=["experiences"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[ExperienceOut])
async def list_experiences(
    application_id: int | None = None,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[ExperienceOut]:
    user = await session.get(User, user_id)
    app_ids = await permitted_app_ids(session, user_id, user.role)
    stmt = (
        select(Experience, Application.name)
        .join(Application, Application.id == Experience.application_id)
        .order_by(Experience.updated_at.desc())
    )
    if application_id is not None:
        stmt = stmt.where(Experience.application_id == application_id)
    if app_ids is not None:
        stmt = stmt.where(Experience.application_id.in_(app_ids))

    rows = (await session.execute(stmt)).all()
    return [
        ExperienceOut(
            id=m.id,
            application_id=m.application_id,
            application_name=app_name,
            trigger_signature=m.trigger_signature,
            content=m.content,
            is_valid=m.is_valid,
            created_at=m.created_at,
        )
        for m, app_name in rows
    ]
