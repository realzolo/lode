"""Application routes: list (dashboard) and detail (settings tabs).

**Two privilege tiers:**

- *Read* endpoints (list/get) require any authenticated user.
- *Write* endpoints that mutate application configuration (Kafka topic,
  bound repositories, preset prompts, data sources) are **admin only**.
  All write endpoints delegate to ``require_admin``; the rest of the
  request shape is validated by the pydantic ``*In`` schemas in
  ``lode.api.schemas``.

The per-application AI model config is *not* owned by this router — it
lives under ``/settings/ai-models`` with ``scope=application`` (see
``routes/settings.py``) and a partial unique index that guarantees at
most one default model per (scope, application_id).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import require_admin, require_user
from lode.api.schemas import (
    ApplicationDetailOut,
    ApplicationOut,
    ApplicationRepoOut,
    ApplicationTopicOut,
    BindRepoIn,
    CreateApplicationIn,
    CreateDbSourceIn,
    CreatePresetPromptIn,
    DbSourceOut,
    PresetPromptOut,
    SetApplicationTopicIn,
)
from lode.db.models.alert import Alert
from lode.db.models.application import (
    Application,
    ApplicationKafka,
    ApplicationRepo,
    DbSource,
    PresetPrompt,
)
from lode.db.models.git import GitRepo
from lode.db.session import AsyncSessionLocal

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
                "id": app_repo.id,
                "repo_id": app_repo.repo_id,
                "name": repo.name,
                "url": repo.repo_url,
                "description": app_repo.description,
            }
            for app_repo, repo in repos
        ],
        preset_prompts=[
            {"id": p.id, "type": p.type, "content": p.content} for p in prompts
        ],
        db_sources=[
            {
                "id": s.id,
                "name": s.name,
                "conn_secret_ref": s.conn_secret_ref,
                "allowed_tables": list(s.allowed_tables or []),
            }
            for s in sources
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


# ---------------------------------------------------------------------------
# Application configuration writes (admin only)
# ---------------------------------------------------------------------------
#
# The Settings tabs render these as <Card> + form UIs. Every endpoint in this
# block is admin gated. Conflicts (unique topic, duplicate repo binding) are
# surfaced as 409 with a friendly detail; missing parents return 404.


@router.put(
    "/{application_id}/topic",
    response_model=ApplicationTopicOut,
)
async def set_application_topic(
    application_id: int,
    payload: SetApplicationTopicIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ApplicationTopicOut:
    """Bind / unbind / replace the Kafka topic for an application.

    Topics are globally unique across applications (see
    ``ApplicationKafka.topic`` unique constraint); switching applications to a
    topic that's already bound to a *different* application returns 409.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    if payload.topic is None:
        # Detach: any existing binding is removed. The Kafka consumer reads
        # ``application_kafka`` on every alert, so a delete here is immediately
        # visible to ingest.
        existing = await session.get(ApplicationKafka, application_id)
        if existing is not None:
            await session.delete(existing)
            await session.commit()
        return ApplicationTopicOut(application_id=application_id, topic=None)

    # Upsert: if no row exists for this app, insert; otherwise update in place.
    existing = await session.get(ApplicationKafka, application_id)
    if existing is None:
        existing = ApplicationKafka(application_id=application_id, topic=payload.topic)
        session.add(existing)
    else:
        existing.topic = payload.topic
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"topic '{payload.topic}' is already bound to another application",
        )
    await session.refresh(existing)
    return ApplicationTopicOut(application_id=application_id, topic=existing.topic)


@router.post(
    "/{application_id}/repos",
    response_model=ApplicationRepoOut,
    status_code=201,
)
async def bind_repo(
    application_id: int,
    payload: BindRepoIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ApplicationRepoOut:
    """Bind a globally registered ``GitRepo`` to the application.

    Admin may also select any *global* repo — there is no app-local repo
    registry. Duplicate bindings (unique on ``(application_id, repo_id)``)
    surface as 409; binding a non-existent repo as 404.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    repo = await session.get(GitRepo, payload.repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"repo {payload.repo_id} not found in registry")

    row = ApplicationRepo(
        application_id=application_id,
        repo_id=payload.repo_id,
        description=payload.description,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"repo {payload.repo_id} is already bound to this application",
        )
    await session.refresh(row)
    return ApplicationRepoOut(
        id=row.id,
        application_id=row.application_id,
        repo_id=row.repo_id,
        repo_name=repo.name,
        repo_url=repo.repo_url,
        description=row.description,
    )


@router.delete(
    "/{application_id}/repos/{repo_id}",
    status_code=204,
)
async def unbind_repo(
    application_id: int,
    repo_id: int,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove an application repo binding. 404 if not bound."""
    row = (
        await session.execute(
            select(ApplicationRepo).where(
                ApplicationRepo.application_id == application_id,
                ApplicationRepo.repo_id == repo_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="repo binding not found")
    await session.delete(row)
    await session.commit()


@router.post(
    "/{application_id}/db-sources",
    response_model=DbSourceOut,
    status_code=201,
)
async def create_db_source(
    application_id: int,
    payload: CreateDbSourceIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DbSourceOut:
    """Add an application-scoped read-only data source.

    The DSN itself lives in the environment (``conn_secret_ref`` is an
    ``env://NAME`` ref or similar); ``allowed_tables`` is the SQL whitelist
    the analysis engine respects when querying.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    row = DbSource(
        application_id=application_id,
        name=payload.name,
        conn_secret_ref=payload.conn_secret_ref,
        allowed_tables=payload.allowed_tables,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DbSourceOut(
        id=row.id,
        application_id=row.application_id,
        name=row.name,
        conn_secret_ref=row.conn_secret_ref,
        allowed_tables=list(row.allowed_tables or []),
    )


@router.delete(
    "/{application_id}/db-sources/{source_id}",
    status_code=204,
)
async def delete_db_source(
    application_id: int,
    source_id: int,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(DbSource, source_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="data source not found")
    await session.delete(row)
    await session.commit()


@router.post(
    "/{application_id}/prompts",
    response_model=PresetPromptOut,
    status_code=201,
)
async def create_preset_prompt(
    application_id: int,
    payload: CreatePresetPromptIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> PresetPromptOut:
    """Add a preset prompt (deploy / other) the analysis engine consults first."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    row = PresetPrompt(
        application_id=application_id,
        type=payload.type,
        content=payload.content,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return PresetPromptOut(
        id=row.id,
        application_id=row.application_id,
        type=row.type,
        content=row.content,
    )


@router.delete(
    "/{application_id}/prompts/{prompt_id}",
    status_code=204,
)
async def delete_preset_prompt(
    application_id: int,
    prompt_id: int,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(PresetPrompt, prompt_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="prompt not found")
    await session.delete(row)
    await session.commit()
