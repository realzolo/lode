"""Application routes: list (dashboard) and detail (settings tabs).

**Two privilege tiers:**

- *Read* endpoints (list/get) require any authenticated user.
- *Write* endpoints that mutate application configuration (Kafka topic,
  bound repositories, descriptions, data sources, model selection) are
  **admin only**.
  All write endpoints delegate to ``require_admin``; the rest of the
  request shape is validated by the pydantic ``*In`` schemas in
  ``lode.api.schemas``.

Applications can select one globally registered AI model config. Model configs
themselves stay owned by ``/settings/ai-models``.
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
from lode.api.audit import audit_action
from lode.api.schemas import (
    AppMemberIn,
    AppMemberOut,
    AppMemberUpdateIn,
    ApplicationDetailOut,
    ApplicationDescriptionOut,
    ApplicationModelOut,
    ApplicationOut,
    ApplicationRepoOut,
    ApplicationTopicOut,
    BindRepoIn,
    CreateApplicationIn,
    CreateApplicationDescriptionIn,
    CreateApplicationRepoIn,
    CreateDbSourceIn,
    DbSourceListItem,
    DbSourceOut,
    SetApplicationModelIn,
    SetApplicationTopicIn,
    UpdateDbSourceIn,
)
from lode.db.models.alert import Alert
from lode.db.models.ai_model import AiModelConfig
from lode.db.models.application import (
    Application,
    ApplicationDescription,
    ApplicationKafka,
    ApplicationRepo,
    DbSource,
)
from lode.db.models.git import GitCredential, GitRepo
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
    descriptions = (
        await session.execute(
            select(ApplicationDescription).where(
                ApplicationDescription.application_id == application_id
            )
        )
    ).scalars().all()
    sources = (
        await session.execute(select(DbSource).where(DbSource.application_id == application_id))
    ).scalars().all()

    return ApplicationDetailOut(
        id=app.id,
        name=app.name,
        topic=topic,
        model_config_id=app.model_config_id,
        created_at=app.created_at,
        repos=[
            {
                "id": app_repo.id,
                "repo_id": app_repo.repo_id,
                "name": repo.name,
                "url": repo.repo_url,
                "scope": repo.scope,
                "repo_type": repo.repo_type,
                "default_branch": repo.default_branch,
                "description": app_repo.description,
            }
            for app_repo, repo in repos
        ],
        descriptions=[
            {
                "id": d.id,
                "description_type": d.description_type,
                "content": d.content,
            }
            for d in descriptions
        ],
        db_sources=[
            DbSourceListItem(
                id=s.id,
                application_id=s.application_id,
                name=s.name,
                description=s.description,
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

    Only the name is required up-front; the Kafka topic, repos, descriptions,
    data sources, and model selection are configured later via the per-app
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
    await audit_action(
        action="application.create",
        actor_id=user_id,
        target_type="application",
        target_id=str(app.id),
        application_id=app.id,
    )
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
        await audit_action(
            action="application.set_topic",
            actor_id=_admin,
            target_type="application",
            target_id=str(application_id),
            application_id=application_id,
            detail={"topic": None},
        )
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
    await audit_action(
        action="application.set_topic",
        actor_id=_admin,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        detail={"topic": existing.topic},
    )
    return ApplicationTopicOut(application_id=application_id, topic=existing.topic)


@router.put(
    "/{application_id}/model",
    response_model=ApplicationModelOut,
)
async def set_application_model(
    application_id: int,
    payload: SetApplicationModelIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ApplicationModelOut:
    """Select a globally supported model for this application, or clear it."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    if payload.model_config_id is not None:
        model = await session.get(AiModelConfig, payload.model_config_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model config not found")

    app.model_config_id = payload.model_config_id
    await session.commit()
    await audit_action(
        action="application.set_model",
        actor_id=_admin,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        detail={"model_config_id": payload.model_config_id},
    )
    return ApplicationModelOut(
        application_id=application_id,
        model_config_id=app.model_config_id,
    )


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
    """Bind a globally registered ``GitRepo`` to the application."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    repo = await session.get(GitRepo, payload.repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"repo {payload.repo_id} not found in registry")
    if repo.scope != "global":
        raise HTTPException(status_code=404, detail=f"repo {payload.repo_id} not found in global registry")

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
    await audit_action(
        action="application.bind_repo",
        actor_id=_admin,
        target_type="git_repo",
        target_id=str(payload.repo_id),
        application_id=application_id,
    )
    return ApplicationRepoOut(
        id=row.id,
        application_id=row.application_id,
        repo_id=row.repo_id,
        repo_name=repo.name,
        repo_url=repo.repo_url,
        repo_scope=repo.scope,
        repo_type=repo.repo_type,
        default_branch=repo.default_branch,
        description=row.description,
    )


@router.post(
    "/{application_id}/repos/local",
    response_model=ApplicationRepoOut,
    status_code=201,
)
async def create_local_repo(
    application_id: int,
    payload: CreateApplicationRepoIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ApplicationRepoOut:
    """Register a repository that is visible only inside this application."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    await _assert_git_credential(session, payload.credential_id)

    repo = GitRepo(
        name=payload.name,
        repo_url=payload.repo_url,
        default_branch=payload.default_branch,
        repo_type=payload.repo_type,
        credential_id=payload.credential_id,
        scope="application",
        application_id=application_id,
    )
    session.add(repo)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="repo_url already registered for this application",
        )

    row = ApplicationRepo(
        application_id=application_id,
        repo_id=repo.id,
        description=payload.description,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await audit_action(
        action="application.create_local_repo",
        actor_id=_admin,
        target_type="git_repo",
        target_id=str(repo.id),
        application_id=application_id,
    )
    return ApplicationRepoOut(
        id=row.id,
        application_id=row.application_id,
        repo_id=row.repo_id,
        repo_name=repo.name,
        repo_url=repo.repo_url,
        repo_scope=repo.scope,
        repo_type=repo.repo_type,
        default_branch=repo.default_branch,
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
    row_repo = (
        await session.execute(
            select(ApplicationRepo, GitRepo)
            .join(GitRepo, GitRepo.id == ApplicationRepo.repo_id)
            .where(
                ApplicationRepo.application_id == application_id,
                ApplicationRepo.repo_id == repo_id,
            )
        )
    ).first()
    if row_repo is None:
        raise HTTPException(status_code=404, detail="repo binding not found")
    row, repo = row_repo
    await session.delete(row)
    if repo.scope == "application" and repo.application_id == application_id:
        await session.flush()
        await session.delete(repo)
    await session.commit()
    await audit_action(
        action="application.unbind_repo",
        actor_id=_admin,
        target_type="git_repo",
        target_id=str(repo_id),
        application_id=application_id,
    )


async def _assert_git_credential(
    session: AsyncSession, credential_id: int | None
) -> None:
    if credential_id is None:
        return
    cred = await session.get(GitCredential, credential_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="git credential not found")


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
        description=payload.description,
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
    await audit_action(
        action="db_source.create",
        actor_id=_admin,
        target_type="db_source",
        target_id=str(row.id),
        application_id=application_id,
        detail={"name": row.name},
    )
    return DbSourceOut(
        id=row.id,
        application_id=row.application_id,
        name=row.name,
        description=row.description,
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
    await audit_action(
        action="db_source.delete",
        actor_id=_admin,
        target_type="db_source",
        target_id=str(source_id),
        application_id=application_id,
    )


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
    if payload.description is not None:
        row.description = payload.description
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
    await audit_action(
        action="db_source.update",
        actor_id=_admin,
        target_type="db_source",
        target_id=str(source_id),
        application_id=application_id,
    )
    return DbSourceOut(
        id=row.id,
        application_id=row.application_id,
        name=row.name,
        description=row.description,
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
        await audit_action(
            action="db_source.test",
            actor_id=_admin,
            target_type="application",
            target_id=str(application_id),
            application_id=application_id,
            result="error",
            detail={"error": str(exc)},
        )
        return {"ok": False, "latency_ms": None, "error": str(exc)}
    await audit_action(
        action="db_source.test",
        actor_id=_admin,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        result="ok",
    )
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
    "/{application_id}/descriptions",
    response_model=ApplicationDescriptionOut,
    status_code=201,
)
async def create_application_description(
    application_id: int,
    payload: CreateApplicationDescriptionIn,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ApplicationDescriptionOut:
    """Add an application description (deploy / other) for the analysis engine."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    row = ApplicationDescription(
        application_id=application_id,
        description_type=payload.description_type,
        content=payload.content,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await audit_action(
        action="application_description.create",
        actor_id=_admin,
        target_type="application_description",
        target_id=str(row.id),
        application_id=application_id,
        detail={"description_type": row.description_type},
    )
    return ApplicationDescriptionOut(
        id=row.id,
        application_id=row.application_id,
        description_type=row.description_type,
        content=row.content,
    )


@router.delete(
    "/{application_id}/descriptions/{description_id}",
    status_code=204,
)
async def delete_application_description(
    application_id: int,
    description_id: int,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(ApplicationDescription, description_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="description not found")
    await session.delete(row)
    await session.commit()
    await audit_action(
        action="application_description.delete",
        actor_id=_admin,
        target_type="application_description",
        target_id=str(description_id),
        application_id=application_id,
    )


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
    await audit_action(
        action="member.add",
        actor_id=_auth,
        target_type="member",
        target_id=str(payload.user_id),
        application_id=application_id,
        detail={"perm": payload.perm},
    )
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
    await audit_action(
        action="member.update",
        actor_id=_auth,
        target_type="member",
        target_id=str(user_id),
        application_id=application_id,
        detail={"perm": payload.perm},
    )
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
    await audit_action(
        action="member.remove",
        actor_id=_auth,
        target_type="member",
        target_id=str(user_id),
        application_id=application_id,
    )
