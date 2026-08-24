"""Alert routes (raw ingestion feed)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.schemas import AlertListOut
from lode.db.models.alert import Alert
from lode.db.models.application import Application
from lode.db.session import AsyncSessionLocal

router = APIRouter(prefix="/alerts", tags=["alerts"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[AlertListOut])
async def list_alerts(
    application_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[AlertListOut]:
    stmt = (
        select(Alert, Application.name)
        .join(Application, Application.id == Alert.application_id)
        .order_by(Alert.received_at.desc())
        .limit(limit)
    )
    if application_id is not None:
        stmt = stmt.where(Alert.application_id == application_id)

    rows = (await session.execute(stmt)).all()
    return [
        AlertListOut(
            id=a.id,
            dedupe_key=a.dedupe_key,
            application_id=a.application_id,
            application_name=app_name,
            topic=a.topic,
            title=a.title,
            level=a.level,
            error_message=a.error_message,
            received_at=a.received_at,
        )
        for a, app_name in rows
    ]
