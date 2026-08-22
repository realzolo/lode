"""Application routes: list (dashboard) and detail (settings tabs)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from incident_trace.api.deps import require_user
from incident_trace.api.schemas import (
    ApplicationDetailOut,
    ApplicationOut,
    CreateApplicationIn,
)
from incident_trace.db.models.alert import Alert
from incident_trace.db.models.application import (
    Application,
    ApplicationKafka,
    ApplicationRepo,
    DbSource,
    PresetPrompt,
)
from incident_trace.db.models.git import GitRepo
from incident_trace.db.session import AsyncSessionLocal

router = APIRouter(prefix="/applications", tags=["applications"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[ApplicationOut])
async def list_applications(
    session: AsyncSession = Depends(get_session),
) -> list[ApplicationOut]:
    stmt = (
        select(
            Application,
            ApplicationKafka.topic,
            func.count(ApplicationRepo.id).label("repo_count"),
        )
        .outerjoin(ApplicationKafka, ApplicationKafka.application_id == Application.id)
        .outerjoin(ApplicationRepo, ApplicationRepo.application_id == Application.id)
        .group_by(Application.id, ApplicationKafka.topic)
        .order_by(Application.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    out: list[ApplicationOut] = []
    for app, topic, repo_count in rows:
        latest_level = await session.execute(
            select(Alert.level)
            .where(Alert.application_id == app.id)
            .order_by(Alert.received_at.desc())
            .limit(1)
        )
        level = latest_level.scalar_one_or_none() or "WARNING"
        out.append(
            ApplicationOut(
                id=app.id,
                name=app.name,
                topic=topic,
                latest_level=level,
                repo_count=repo_count or 0,
                created_at=app.created_at,
            )
        )
    return out


@router.get("/{application_id}", response_model=ApplicationDetailOut)
async def get_application(
    application_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApplicationDetailOut:
    app = (
        await session.execute(select(Application).where(Application.id == application_id))
    ).scalars().first()
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    topic = (
        await session.execute(
            select(ApplicationKafka.topic).where(ApplicationKafka.application_id == application_id)
        )
    ).scalar_one_or_none()

    repos = (
        await session.execute(
            select(ApplicationRepo, GitRepo)
            .join(GitRepo, GitRepo.id == ApplicationRepo.repo_id)
            .where(ApplicationRepo.application_id == application_id)
        )
    ).all()
    prompts = (
        await session.execute(
            select(PresetPrompt).where(PresetPrompt.application_id == application_id)
        )
    ).scalars().all()
    sources = (
        await session.execute(select(DbSource).where(DbSource.application_id == application_id))
    ).scalars().all()

    return ApplicationDetailOut(
        id=app.id,
        name=app.name,
        topic=topic,
        created_at=app.created_at,
        repos=[
            {
                "name": repo.name,
                "url": repo.repo_url,
                "description": app_repo.description,
            }
            for app_repo, repo in repos
        ],
        preset_prompts=[
            {"type": p.type, "content": p.content} for p in prompts
        ],
        db_sources=[
            {"name": s.name, "allowed_tables": s.allowed_tables or []} for s in sources
        ],
    )


@router.post("", response_model=ApplicationOut, status_code=201)
async def create_application(
    payload: CreateApplicationIn,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ApplicationOut:
    """Create a new application (isolation unit).

    Only the name is required up-front; the Kafka topic, repos, preset prompts,
    data sources, and model override are configured later via the per-app
    settings tabs. ``created_by`` is stamped from the authenticated caller.
    """
    app = Application(name=payload.name, created_by=user_id)
    session.add(app)
    await session.commit()
    await session.refresh(app)
    return ApplicationOut(
        id=app.id,
        name=app.name,
        topic=None,
        latest_level="WARNING",
        repo_count=0,
        created_at=app.created_at,
    )
