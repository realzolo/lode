"""Shared memory routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.schemas import MemoryOut
from lode.db.models.application import Application
from lode.db.models.memory import Memory
from lode.db.session import AsyncSessionLocal

router = APIRouter(prefix="/memories", tags=["memories"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    application_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[MemoryOut]:
    stmt = (
        select(Memory, Application.name)
        .join(Application, Application.id == Memory.application_id)
        .order_by(Memory.updated_at.desc())
    )
    if application_id is not None:
        stmt = stmt.where(Memory.application_id == application_id)

    rows = (await session.execute(stmt)).all()
    return [
        MemoryOut(
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
