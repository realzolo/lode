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

from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import (
    permitted_app_ids,
    require_admin,
    require_app_perm,
    require_user,
)
from lode.api.schemas import (
    AppMemberIn,
    AppMemberOut,
    AppMemberUpdateIn,
    ApplicationDetailOut,
    ApplicationOut,
    ApplicationRepoOut,
    ApplicationTopicOut,
    BindRepoIn,
    CreateApplicationIn,
    CreateDbSourceIn,
    CreatePresetPromptIn,
    DbSourceListItem,
    DbSourceOut,
    PresetPromptOut,
    SetApplicationTopicIn,
    UpdateDbSourceIn,
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
from lode.db.models.permission import UserApplicationPerm
from lode.crypto import encrypt_secret
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine.db_proxy import test_connection

router = APIRouter(prefix="/applications", tags=["applications"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[ApplicationOut])
async def list_applications(
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[ApplicationOut]:
    user = await session.get(User, user_id)
    app_ids = await permitted_app_ids(session, user_id, user.role)
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
    if app_ids is not None:
        stmt = stmt.where(Application.id.in_(app_ids))
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
    _auth: int = Security(require_app_perm, scopes=["read"]),
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
            DbSourceListItem(
                id=s.id,
                application_id=s.application_id,
                name=s.name,
                conn_secret_ref=s.conn_secret_ref,
                host=s.host,
                port=s.port,
                database=s.database,
                username=s.username,
                has_password=bool(s.password),
                sslmode=s.sslmode,
                allowed_tables=list(s.allowed_tables or []),
                sensitive_columns=list(s.sensitive_columns or []),
            )
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
    # Flush so the generated application id is available for the perm row.
    await session.flush()
    # The creator becomes an application admin so they appear in the Members
    # list and can manage the application's membership from the start.
    session.add(
        UserApplicationPerm(
            user_id=user_id, application_id=app.id, perm="admin"
        )
    )
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

    Two connection modes are supported (the schema enforces that exactly one
    is supplied):

    * **Structured** — ``host`` / ``port`` / ``database`` / ``username`` /
      ``password`` are stored on the row and the DSN is built at query time.
      ``sslmode`` forces TLS when the replica is reached over a network.
    * **Secret ref** — ``conn_secret_ref`` (``env://NAME`` / bare DSN) keeps
      the real credentials in the deployment environment rather than this row.

    ``allowed_tables`` is the SQL whitelist the analysis engine respects when
    querying this source; ``sensitive_columns`` are extra result columns masked
    on top of the built-in heuristic.
    """
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    row = DbSource(
        application_id=application_id,
        name=payload.name,
        conn_secret_ref=payload.conn_secret_ref,
        host=payload.host,
        port=payload.port,
        database=payload.database,
        username=payload.username,
        password=encrypt_secret(payload.password),
        sslmode=payload.sslmode,
        allowed_tables=payload.allowed_tables,
        sensitive_columns=payload.sensitive_columns,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DbSourceOut(
        id=row.id,
        application_id=row.application_id,
        name=row.name,
        conn_secret_ref=row.conn_secret_ref,
        host=row.host,
        port=row.port,
        database=row.database,
        username=row.username,
        has_password=bool(row.password),
        sslmode=row.sslmode,
        allowed_tables=list(row.allowed_tables or []),
        sensitive_columns=list(row.sensitive_columns or []),
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


@router.put(
    "/{application_id}/db-sources/{source_id}",
    response_model=DbSourceOut,
)
async def update_db_source(
    application_id: int,
    source_id: int,
    payload: UpdateDbSourceIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DbSourceOut:
    """Update an existing data source.

    All fields are optional. The stored password is only replaced when a
    non-empty ``password`` is supplied, so metadata can be rotated without
    re-pasting the secret. Supplying neither a structured connection nor a
    secret ref leaves the existing connection mode untouched.
    """
    row = await session.get(DbSource, source_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="data source not found")

    if payload.name is not None:
        row.name = payload.name
    if payload.host is not None:
        row.host = payload.host
    if payload.port is not None:
        row.port = payload.port
    if payload.database is not None:
        row.database = payload.database
    if payload.username is not None:
        row.username = payload.username
    if payload.password:
        row.password = encrypt_secret(payload.password)
    if payload.conn_secret_ref is not None:
        row.conn_secret_ref = payload.conn_secret_ref
    if payload.sslmode is not None:
        row.sslmode = payload.sslmode
    if payload.allowed_tables is not None:
        row.allowed_tables = payload.allowed_tables
    if payload.sensitive_columns is not None:
        row.sensitive_columns = payload.sensitive_columns

    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DbSourceOut(
        id=row.id,
        application_id=row.application_id,
        name=row.name,
        conn_secret_ref=row.conn_secret_ref,
        host=row.host,
        port=row.port,
        database=row.database,
        username=row.username,
        has_password=bool(row.password),
        sslmode=row.sslmode,
        allowed_tables=list(row.allowed_tables or []),
        sensitive_columns=list(row.sensitive_columns or []),
    )


@router.post(
    "/{application_id}/db-sources/test",
    status_code=200,
)
async def test_db_source_connection(
    application_id: int,
    payload: CreateDbSourceIn,
    _admin: int = Depends(require_admin),
) -> dict:
    """Validate a structured/secret-ref connection without persisting it.

    Lets an admin catch a typo in host/port/credentials (or a missing TLS
    setup) *before* saving. Secret refs resolve through the same path as a real
    query; a ``vault://`` reference is rejected closed, exactly like at query
    time. Returns ``{ok, latency_ms, error}``.
    """
    dsn = _resolve_create_dsn(payload)
    try:
        latency = await test_connection(dsn)
    except Exception as exc:  # surfaced as a structured result, not 500
        return {"ok": False, "latency_ms": None, "error": str(exc)}
    return {"ok": True, "latency_ms": round(latency * 1000, 1), "error": None}


def _resolve_create_dsn(payload: CreateDbSourceIn) -> str:
    """Build the DSN a create/test payload would resolve to.

    Mirrors the query-time resolution so the test exercises the exact
    connection string the engine would use.
    """
    from lode.engine.db_proxy import resolve_dsn

    return resolve_dsn(
        payload.conn_secret_ref,
        host=payload.host,
        port=payload.port,
        database=payload.database,
        username=payload.username,
        password=payload.password,
    )


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


# ---------------------------------------------------------------------------
# Application membership (admin or app-admin)
# ---------------------------------------------------------------------------
#
# These back the per-application Members tab. Every endpoint is guarded by
# ``require_app_perm`` scope "admin", so both global admins and the
# application's own admins can manage membership; readers and analysts
# cannot. ``UserApplicationPerm`` uses a composite primary key of
# (user_id, application_id), so "add" upserts an existing membership's
# perm level rather than failing on a duplicate.


@router.get(
    "/{application_id}/members",
    response_model=list[AppMemberOut],
)
async def list_members(
    application_id: int,
    _auth: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> list[AppMemberOut]:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    rows = (
        await session.execute(
            select(UserApplicationPerm, User)
            .join(User, User.id == UserApplicationPerm.user_id)
            .where(UserApplicationPerm.application_id == application_id)
            .order_by(User.email)
        )
    ).all()
    return [
        AppMemberOut(
            user_id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status,
            perm=perm.perm,
        )
        for perm, user in rows
    ]


@router.post(
    "/{application_id}/members",
    response_model=AppMemberOut,
    status_code=201,
)
async def add_member(
    application_id: int,
    payload: AppMemberIn,
    _auth: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> AppMemberOut:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    user = await session.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    # Upsert: an existing membership simply moves to the new perm level.
    existing = await session.get(
        UserApplicationPerm, (payload.user_id, application_id)
    )
    if existing is None:
        existing = UserApplicationPerm(
            user_id=payload.user_id,
            application_id=application_id,
            perm=payload.perm,
        )
        session.add(existing)
    else:
        existing.perm = payload.perm
    await session.commit()
    await session.refresh(existing)
    return AppMemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        perm=existing.perm,
    )


@router.put(
    "/{application_id}/members/{user_id}",
    response_model=AppMemberOut,
)
async def update_member(
    application_id: int,
    user_id: int,
    payload: AppMemberUpdateIn,
    _auth: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> AppMemberOut:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    row = await session.get(UserApplicationPerm, (user_id, application_id))
    if row is None:
        raise HTTPException(status_code=404, detail="membership not found")
    row.perm = payload.perm
    await session.commit()
    await session.refresh(row)
    user = await session.get(User, user_id)
    return AppMemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        perm=row.perm,
    )


@router.delete(
    "/{application_id}/members/{user_id}",
    status_code=204,
)
async def remove_member(
    application_id: int,
    user_id: int,
    _auth: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> None:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    row = await session.get(UserApplicationPerm, (user_id, application_id))
    if row is None:
        raise HTTPException(status_code=404, detail="membership not found")

    # Guard: never leave an application without at least one admin member.
    if row.perm == "admin":
        admin_count = (
            await session.execute(
                select(UserApplicationPerm.user_id).where(
                    UserApplicationPerm.application_id == application_id,
                    UserApplicationPerm.perm == "admin",
                )
            )
        ).scalars().all()
        if len(admin_count) <= 1:
            raise HTTPException(
                status_code=409,
                detail="cannot remove the last admin member of this application",
            )
    await session.delete(row)
    await session.commit()
