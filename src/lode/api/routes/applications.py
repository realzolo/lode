"""Application routes: list (dashboard) and detail (settings tabs).

**Two privilege tiers:**

- *Read* endpoints (list/get) require any authenticated user.
- *Write* endpoints that mutate application-owned configuration use the
  application's ``admin`` permission; global admins always satisfy it.
- Global catalogs and platform settings remain global-admin only.

Applications can select one globally registered AI model config. Model configs
themselves stay owned by ``/settings/ai-models``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from time import monotonic

from aiokafka import AIOKafkaProducer
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
    ApplicationIntegrationConfigurationOut,
    ApplicationIntegrationIn,
    ApplicationIntegrationOut,
    ApplicationIntegrationUpdateIn,
    ApplicationDetailOut,
    ApplicationArchitectureContextOut,
    ApplicationIngestionStatusOut,
    ApplicationModelOut,
    ApplicationOut,
    ApplicationRepoOut,
    ApplicationServiceBindingIn,
    ApplicationServiceBindingOut,
    ApplicationTopicOut,
    BindRepoIn,
    CreateApplicationIn,
    CreateApplicationArchitectureContextIn,
    CreateApplicationRepoIn,
    RunApprovedQueryIn,
    SetApplicationModelIn,
    SetApplicationTopicIn,
    StartApplicationIngestionIn,
    UserOut,
)
from lode.db.models.alert import Alert
from lode.db.models.ai_model import AiModelConfig
from lode.db.models.application import (
    Application,
    ApplicationArchitectureContext,
    ApplicationIngestionRuntime,
    ApplicationRepo,
    ApplicationServiceBinding,
    Service,
)
from lode.db.models.integration import ApplicationIntegration
from lode.config import kafka_security_kwargs, settings
from lode.db.models.git import GitCredential, GitRepo
from lode.db.models.permission import UserApplicationPerm
from lode.crypto import encrypt_secret
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine.db_proxy import DbProxyError, execute_approved_query
from lode.engine.integrations import IntegrationError, connector_for, resolve_integration_secrets
from lode.engine.model_health import probe_model, record_model_health
from lode.integration_policy import (
    integration_kind,
    normalize_integration_config,
    normalize_integration_secrets,
)

router = APIRouter(prefix="/applications", tags=["applications"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def _runtime_status(
    app: Application, runtime: ApplicationIngestionRuntime | None
) -> str:
    if app.ingestion_state == "draft":
        return "draft"
    if app.ingestion_state == "paused":
        return "paused"
    if runtime is None or runtime.observed_version != app.ingestion_version:
        return "starting"
    if runtime.observed_state == "error" or runtime.last_heartbeat_at is None:
        return "error" if runtime is not None and runtime.last_error else "starting"
    age = (datetime.now(UTC) - runtime.last_heartbeat_at).total_seconds()
    if age > settings.kafka_runtime_stale_seconds:
        return "error"
    return "listening" if runtime.observed_state == "listening" else "starting"


def _ingestion_status_out(
    app: Application,
    runtime: ApplicationIngestionRuntime | None,
) -> ApplicationIngestionStatusOut:
    return ApplicationIngestionStatusOut(
        application_id=app.id,
        ingestion_topic=app.ingestion_topic,
        desired_state=app.ingestion_state,
        observed_state=_runtime_status(app, runtime),
        ingestion_version=app.ingestion_version,
        start_position=app.ingestion_start_position,
        assigned_partitions=runtime.assigned_partitions if runtime is not None else 0,
        backlog=runtime.backlog if runtime is not None else None,
        last_heartbeat_at=runtime.last_heartbeat_at if runtime is not None else None,
        last_error=runtime.last_error if runtime is not None else None,
    )


async def _runtime_for(
    session: AsyncSession, application_id: int
) -> ApplicationIngestionRuntime | None:
    return await session.get(ApplicationIngestionRuntime, application_id)


async def _ensure_runtime(
    session: AsyncSession, app: Application
) -> ApplicationIngestionRuntime:
    runtime = await _runtime_for(session, app.id)
    if runtime is None:
        runtime = ApplicationIngestionRuntime(application_id=app.id)
        session.add(runtime)
    return runtime


async def _validate_kafka_topic(topic: str) -> None:
    """Ensure the broker currently exposes at least one partition for ``topic``."""
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        **kafka_security_kwargs(),
    )
    try:
        await producer.start()
        partitions = await asyncio.wait_for(
            producer.partitions_for(topic),
            timeout=settings.kafka_topic_validation_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=503, detail="Kafka topic validation timed out") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - return an actionable control-plane error
        raise HTTPException(status_code=503, detail=f"Kafka topic validation failed: {exc}") from exc
    finally:
        await producer.stop()
    if not partitions:
        raise HTTPException(status_code=422, detail=f"Kafka topic '{topic}' has no partitions")


async def _require_ingestion_readiness(
    session: AsyncSession,
    app: Application,
) -> str:
    """Fail closed unless every application-level ingestion prerequisite exists."""
    primary_service_count = await session.scalar(
        select(func.count(ApplicationServiceBinding.service_id))
        .join(Service, Service.id == ApplicationServiceBinding.service_id)
        .where(
            ApplicationServiceBinding.application_id == app.id,
            ApplicationServiceBinding.role == "primary",
            Service.state == "active",
        )
    )
    model = await session.get(
        AiModelConfig, app.model_config_id
    ) if app.model_config_id is not None else None

    missing: list[str] = []
    if not primary_service_count:
        missing.append("primary_service")
    if model is None:
        missing.append("model")
    elif model.last_test_status != "available":
        missing.append("model_availability")
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "application_not_ready",
                "message": "Complete all required application settings before starting ingestion.",
                "missing": missing,
            },
        )
    return app.ingestion_topic


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
            func.count(func.distinct(ApplicationServiceBinding.service_id)).label("service_count"),
            func.count(ApplicationServiceBinding.service_id)
            .filter(ApplicationServiceBinding.role == "primary")
            .label("primary_service_count"),
            AiModelConfig.last_test_status,
        )
        .outerjoin(
            ApplicationServiceBinding,
            ApplicationServiceBinding.application_id == Application.id,
        )
        .outerjoin(AiModelConfig, AiModelConfig.id == Application.model_config_id)
        .group_by(Application.id, AiModelConfig.last_test_status)
        .order_by(Application.created_at.desc())
    )
    if app_ids is not None:
        stmt = stmt.where(Application.id.in_(app_ids))
    rows = (await session.execute(stmt)).all()

    perms: dict[int, str] = {}
    if user.role != "admin":
        perms = dict(
            (
                await session.execute(
                    select(
                        UserApplicationPerm.application_id,
                        UserApplicationPerm.perm,
                    ).where(UserApplicationPerm.user_id == user_id)
                )
            ).all()
        )

    runtimes = {
        runtime.application_id: runtime
        for runtime in (
            await session.execute(
                select(ApplicationIngestionRuntime).where(
                    ApplicationIngestionRuntime.application_id.in_([app.id for app, *_ in rows])
                )
            )
        ).scalars()
    } if rows else {}

    out: list[ApplicationOut] = []
    for app, service_count, primary_service_count, model_test_status in rows:
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
                ingestion_topic=app.ingestion_topic,
                latest_level=level,
                service_count=service_count or 0,
                primary_service_configured=bool(primary_service_count),
                model_configured=app.model_config_id is not None,
                model_available=model_test_status == "available",
                ingestion_state=app.ingestion_state,
                ingestion_observed_state=_runtime_status(app, runtimes.get(app.id)),
                ingestion_start_position=app.ingestion_start_position,
                my_perm="admin" if user.role == "admin" else perms.get(app.id),
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

    repos = (
        await session.execute(
            select(ApplicationRepo, GitRepo)
            .join(GitRepo, GitRepo.id == ApplicationRepo.repo_id)
            .where(ApplicationRepo.application_id == application_id)
        )
    ).all()
    architecture_contexts = (
        await session.execute(
            select(ApplicationArchitectureContext).where(
                ApplicationArchitectureContext.application_id == application_id
            )
        )
    ).scalars().all()
    integrations = (
        await session.execute(
            select(ApplicationIntegration).where(
                ApplicationIntegration.application_id == application_id
            )
        )
    ).scalars().all()
    current_user = await session.get(User, _auth)
    is_global_admin = current_user is not None and current_user.role == "admin"
    if is_global_admin:
        my_perm = "admin"
    else:
        perm = await session.get(UserApplicationPerm, (_auth, application_id))
        my_perm = perm.perm if perm is not None else None

    return ApplicationDetailOut(
        id=app.id,
        name=app.name,
        ingestion_topic=app.ingestion_topic,
        model_config_id=app.model_config_id,
        ingestion_state=app.ingestion_state,
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
        architecture_contexts=[
            {
                "id": d.id,
                "content": d.content,
            }
            for d in architecture_contexts
        ],
        integrations=[_integration_out(item, include_error=is_global_admin) for item in integrations],
        my_perm=my_perm,
    )


@router.post("", response_model=ApplicationOut, status_code=201)
async def create_application(
    payload: CreateApplicationIn,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ApplicationOut:
    """Create a new application (isolation unit).

    Name and ingestion topic are one atomic invariant. Other application
    settings are configured later via the per-application settings pages.
    """
    app = Application(
        name=payload.name,
        ingestion_topic=payload.ingestion_topic,
        created_by=user_id,
    )
    session.add(app)
    # Flush so the generated application id is available for the perm row.
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"ingestion topic '{payload.ingestion_topic}' is already in use",
        ) from exc
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
        ingestion_topic=app.ingestion_topic,
        latest_level="WARNING",
        service_count=0,
        primary_service_configured=False,
        model_configured=False,
        model_available=False,
        ingestion_state=app.ingestion_state,
        ingestion_observed_state="draft",
        ingestion_start_position=None,
        my_perm="admin",
        created_at=app.created_at,
    )


# ---------------------------------------------------------------------------
# Application configuration writes
# ---------------------------------------------------------------------------
#
# The Settings tabs render these as <Card> + form UIs. Every endpoint in this
# block is admin gated. Conflicts (unique topic, duplicate repo binding) are
# surfaced as 409 with a friendly detail; missing parents return 404.


@router.put(
    "/{application_id}/ingestion-topic",
    response_model=ApplicationTopicOut,
)
async def set_application_topic(
    application_id: int,
    payload: SetApplicationTopicIn,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationTopicOut:
    """Replace an application's required, globally unique ingestion topic."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.ingestion_state == "active":
        raise HTTPException(
            status_code=409,
            detail="pause ingestion before changing its Kafka topic",
        )

    runtime = await _runtime_for(session, application_id)
    app.ingestion_topic = payload.ingestion_topic
    if runtime is not None:
        await session.delete(runtime)
    app.ingestion_state = "draft"
    app.ingestion_start_position = None
    app.ingestion_paused_at = None
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"ingestion topic '{payload.ingestion_topic}' is already in use",
        )
    await audit_action(
        action="application.set_topic",
        actor_id=_admin,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        detail={"ingestion_topic": app.ingestion_topic, "ingestion_state": "draft"},
    )
    return ApplicationTopicOut(
        application_id=application_id, ingestion_topic=app.ingestion_topic
    )


@router.get("/{application_id}/ingestion", response_model=ApplicationIngestionStatusOut)
async def get_application_ingestion(
    application_id: int,
    _auth: int = Security(require_app_perm, scopes=["read"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationIngestionStatusOut:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    return _ingestion_status_out(app, await _runtime_for(session, application_id))


@router.post(
    "/{application_id}/ingestion/start",
    response_model=ApplicationIngestionStatusOut,
    status_code=202,
)
async def start_application_ingestion(
    application_id: int,
    payload: StartApplicationIngestionIn,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationIngestionStatusOut:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.ingestion_state not in {"draft", "paused"} or app.ingestion_start_position is not None:
        raise HTTPException(
            status_code=409,
            detail="only a draft or migrated paused application can be started",
        )

    topic = await _require_ingestion_readiness(session, app)
    await _validate_kafka_topic(topic)

    app.ingestion_state = "active"
    app.ingestion_version += 1
    app.ingestion_start_position = payload.start_position
    app.ingestion_started_at = datetime.now(UTC)
    app.ingestion_paused_at = None
    runtime = await _ensure_runtime(session, app)
    runtime.observed_state = "starting"
    runtime.observed_version = app.ingestion_version
    runtime.consumer_id = None
    runtime.assigned_partitions = 0
    runtime.backlog = None
    runtime.last_heartbeat_at = None
    runtime.last_error = None
    await session.commit()
    await audit_action(
        action="application.ingestion_start",
        actor_id=_admin,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        detail={
            "topic": topic,
            "ingestion_version": app.ingestion_version,
            "start_position": payload.start_position,
        },
    )
    return _ingestion_status_out(app, runtime)


@router.post("/{application_id}/ingestion/pause", response_model=ApplicationIngestionStatusOut)
async def pause_application_ingestion(
    application_id: int,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationIngestionStatusOut:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.ingestion_state != "active":
        raise HTTPException(status_code=409, detail="only an active application can be paused")
    app.ingestion_state = "paused"
    app.ingestion_paused_at = datetime.now(UTC)
    runtime = await _ensure_runtime(session, app)
    runtime.observed_state = "paused"
    runtime.assigned_partitions = 0
    runtime.backlog = runtime.backlog
    runtime.last_error = None
    await session.commit()
    await audit_action(
        action="application.ingestion_pause",
        actor_id=_admin,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        detail={"ingestion_topic": app.ingestion_topic, "ingestion_version": app.ingestion_version},
    )
    return _ingestion_status_out(app, runtime)


@router.post("/{application_id}/ingestion/resume", response_model=ApplicationIngestionStatusOut)
async def resume_application_ingestion(
    application_id: int,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationIngestionStatusOut:
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")
    if app.ingestion_state != "paused":
        raise HTTPException(status_code=409, detail="only a paused application can be resumed")
    topic = await _require_ingestion_readiness(session, app)
    app.ingestion_state = "active"
    app.ingestion_paused_at = None
    runtime = await _ensure_runtime(session, app)
    runtime.observed_state = "starting"
    runtime.observed_version = app.ingestion_version
    runtime.assigned_partitions = 0
    runtime.last_error = None
    await session.commit()
    await audit_action(
        action="application.ingestion_resume",
        actor_id=_admin,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        detail={"topic": topic, "ingestion_version": app.ingestion_version},
    )
    return _ingestion_status_out(app, runtime)


@router.put(
    "/{application_id}/model",
    response_model=ApplicationModelOut,
)
async def set_application_model(
    application_id: int,
    payload: SetApplicationModelIn,
    actor_id: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationModelOut:
    """Select a globally supported model for this application, or clear it."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    model_test = None
    if payload.model_config_id is not None:
        model = await session.get(AiModelConfig, payload.model_config_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model config not found")
        health = await probe_model(model)
        record_model_health(model, health)
        model_test = {
            "available": health.available,
            "endpoint": health.endpoint,
            "latency_ms": health.latency_ms,
            "error_code": health.error_code,
            "error_detail": health.error_detail,
        }
        if not health.available:
            await session.commit()
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "model_unavailable",
                    "message": f"Model availability test failed: {health.error_detail or health.error_code or 'unknown provider error'}",
                    "model_test": model_test,
                },
            )

    app.model_config_id = payload.model_config_id
    await session.commit()
    await audit_action(
        action="application.set_model",
        actor_id=actor_id,
        target_type="application",
        target_id=str(application_id),
        application_id=application_id,
        detail={"model_config_id": payload.model_config_id},
    )
    return ApplicationModelOut(
        application_id=application_id,
        model_config_id=app.model_config_id,
        model_test=model_test,
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


def _integration_out(
    row: ApplicationIntegration, *, include_error: bool = True
) -> ApplicationIntegrationOut:
    """Safe integration projection. Credentials never leave the backend."""
    configured_secret_fields: list[str] = []
    try:
        configured_secret_fields = sorted(resolve_integration_secrets(row.secrets_ciphertext))
    except IntegrationError:
        pass
    return ApplicationIntegrationOut(
        id=row.id,
        application_id=row.application_id,
        name=row.name,
        kind=row.kind,
        kind_version=row.kind_version,
        revision=row.revision,
        state=row.state,
        verification_status=row.verification_status,
        verified_at=row.verified_at,
        last_collected_at=row.last_collected_at,
        last_error=row.last_error if include_error else None,
        configured_secret_fields=configured_secret_fields,
    )


def _integration_configuration_out(
    row: ApplicationIntegration,
) -> ApplicationIntegrationConfigurationOut:
    """Return selectors only on the global-admin configuration surface."""
    return ApplicationIntegrationConfigurationOut(
        **_integration_out(row).model_dump(),
        config=dict(row.config or {}),
    )


@router.get(
    "/{application_id}/integrations",
    response_model=list[ApplicationIntegrationOut],
)
async def list_integrations(
    application_id: int,
    _auth: int = Security(require_app_perm, scopes=["read"]),
    session: AsyncSession = Depends(get_session),
) -> list[ApplicationIntegrationOut]:
    rows = (
        await session.execute(
            select(ApplicationIntegration)
            .where(ApplicationIntegration.application_id == application_id)
            .order_by(ApplicationIntegration.kind, ApplicationIntegration.name)
        )
    ).scalars().all()
    return [_integration_out(row, include_error=False) for row in rows]


async def _verify_integration(
    kind: str, config: dict, secrets: dict[str, str]
) -> None:
    await connector_for(kind).verify_readonly(config, secrets)


@router.get(
    "/{application_id}/integrations/{integration_id}",
    response_model=ApplicationIntegrationConfigurationOut,
)
async def get_integration_configuration(
    application_id: int,
    integration_id: int,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationIntegrationConfigurationOut:
    row = await session.get(ApplicationIntegration, integration_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="integration not found")
    return _integration_configuration_out(row)


@router.post("/{application_id}/integrations/test", response_model=dict)
async def test_integration(
    application_id: int,
    payload: ApplicationIntegrationIn,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Verify a prospective integration without persisting its credential."""
    if await session.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    try:
        started = monotonic()
        await _verify_integration(payload.kind, payload.config, payload.secrets)
    except IntegrationError as exc:
        await audit_action(
            action="integration.test", actor_id=_admin, target_type="application",
            target_id=str(application_id), application_id=application_id,
            result="error", detail={"kind": payload.kind, "error": str(exc)[:280]},
        )
        return {"ok": False, "latency_ms": None, "error": str(exc)}
    await audit_action(
        action="integration.test", actor_id=_admin, target_type="application",
        target_id=str(application_id), application_id=application_id,
        detail={"kind": payload.kind},
    )
    return {"ok": True, "latency_ms": round((monotonic() - started) * 1000, 1), "error": None}


@router.post("/{application_id}/integrations", response_model=ApplicationIntegrationOut, status_code=201)
async def create_integration(
    application_id: int,
    payload: ApplicationIntegrationIn,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationIntegrationOut:
    if await session.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    try:
        await _verify_integration(payload.kind, payload.config, payload.secrets)
    except IntegrationError as exc:
        await audit_action(
            action="integration.create", actor_id=_admin, target_type="application",
            target_id=str(application_id), application_id=application_id,
            result="error", detail={"kind": payload.kind, "error": str(exc)[:280]},
        )
        raise HTTPException(status_code=422, detail=f"read-only verification failed: {exc}")
    row = ApplicationIntegration(
        application_id=application_id, name=payload.name, kind=payload.kind,
        kind_version=integration_kind(payload.kind).version,
        config=payload.config,
        secrets_ciphertext=encrypt_secret(json.dumps(payload.secrets)) or "",
        verification_status="verified", verified_at=datetime.now(UTC),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"integration name '{payload.name}' is already in use for this application",
        ) from exc
    await session.refresh(row)
    await audit_action(
        action="integration.create", actor_id=_admin, target_type="integration",
        target_id=str(row.id), application_id=application_id, detail={"kind": row.kind},
    )
    return _integration_out(row)


@router.put("/{application_id}/integrations/{integration_id}", response_model=ApplicationIntegrationOut)
async def update_integration(
    application_id: int,
    integration_id: int,
    payload: ApplicationIntegrationUpdateIn,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationIntegrationOut:
    row = await session.get(ApplicationIntegration, integration_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="integration not found")
    next_config = (
        normalize_integration_config(row.kind, payload.config)
        if payload.config is not None
        else dict(row.config or {})
    )
    current_secrets = resolve_integration_secrets(row.secrets_ciphertext)
    next_secrets = (
        normalize_integration_secrets(row.kind, {**current_secrets, **payload.secrets})
        if payload.secrets is not None
        else current_secrets
    )
    next_state = payload.state or row.state
    if next_state == "active":
        try:
            await _verify_integration(row.kind, next_config, next_secrets)
        except IntegrationError as exc:
            await audit_action(
                action="integration.update", actor_id=_admin, target_type="integration",
                target_id=str(row.id), application_id=application_id, result="error",
                detail={"error": str(exc)[:280]},
            )
            raise HTTPException(status_code=422, detail=f"read-only verification failed: {exc}")
        row.verification_status = "verified"
        row.verified_at = datetime.now(UTC)
        row.last_error = None
    if payload.name is not None:
        row.name = payload.name.strip()
    row.config = next_config
    row.secrets_ciphertext = encrypt_secret(json.dumps(next_secrets)) or ""
    row.state = next_state
    row.revision += 1
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="integration name already in use") from exc
    await session.refresh(row)
    await audit_action(
        action="integration.update", actor_id=_admin, target_type="integration",
        target_id=str(row.id), application_id=application_id, detail={"state": row.state},
    )
    return _integration_out(row)


@router.delete("/{application_id}/integrations/{integration_id}", status_code=204)
async def delete_integration(
    application_id: int,
    integration_id: int,
    _admin: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(ApplicationIntegration, integration_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="integration not found")
    await session.delete(row)
    await session.commit()
    await audit_action(
        action="integration.delete", actor_id=_admin, target_type="integration",
        target_id=str(integration_id), application_id=application_id,
    )


@router.post("/{application_id}/integrations/{integration_id}/query", response_model=dict)
async def run_integration_query(
    application_id: int,
    integration_id: int,
    payload: RunApprovedQueryIn,
    actor_id: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(ApplicationIntegration, integration_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="integration not found")
    if row.state != "active" or "query_catalog" not in integration_kind(row.kind).capabilities:
        raise HTTPException(status_code=409, detail="integration does not expose query_catalog")
    try:
        result = await execute_approved_query(
            dict(row.config or {}), resolve_integration_secrets(row.secrets_ciphertext),
            table=payload.table, operation=payload.operation,
        )
    except DbProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await audit_action(
        action="integration.query", actor_id=actor_id, target_type="integration",
        target_id=str(row.id), application_id=application_id,
        detail={"operation": payload.operation, "table": payload.table},
    )
    return result


@router.post(
    "/{application_id}/architecture-contexts",
    response_model=ApplicationArchitectureContextOut,
    status_code=201,
)
async def create_application_architecture_context(
    application_id: int,
    payload: CreateApplicationArchitectureContextIn,
    actor_id: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationArchitectureContextOut:
    """Add architecture context that will be snapshotted for new investigations."""
    app = await session.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="application not found")

    row = ApplicationArchitectureContext(
        application_id=application_id,
        content=payload.content,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await audit_action(
        action="application_architecture_context.create",
        actor_id=actor_id,
        target_type="application_architecture_context",
        target_id=str(row.id),
        application_id=application_id,
    )
    return ApplicationArchitectureContextOut(
        id=row.id,
        application_id=row.application_id,
        content=row.content,
    )


@router.delete(
    "/{application_id}/architecture-contexts/{context_id}",
    status_code=204,
)
async def delete_application_architecture_context(
    application_id: int,
    context_id: int,
    actor_id: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(ApplicationArchitectureContext, context_id)
    if row is None or row.application_id != application_id:
        raise HTTPException(status_code=404, detail="architecture context not found")
    await session.delete(row)
    await session.commit()
    await audit_action(
        action="application_architecture_context.delete",
        actor_id=actor_id,
        target_type="application_architecture_context",
        target_id=str(context_id),
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


@router.get(
    "/{application_id}/member-candidates",
    response_model=list[UserOut],
)
async def list_member_candidates(
    application_id: int,
    _auth: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    """Return selectable users for an application-admin membership change.

    This is deliberately application-scoped: a user with ``admin`` permission
    on one application may manage that application's members but must not gain
    access to the platform-wide ``/users`` administration endpoint.
    """
    if await session.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    rows = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    return [
        UserOut(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status,
            created_at=user.created_at,
        )
        for user in rows
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


@router.get(
    "/{application_id}/services",
    response_model=list[ApplicationServiceBindingOut],
)
async def list_application_services(
    application_id: int,
    _auth: int = Security(require_app_perm, scopes=["read"]),
    session: AsyncSession = Depends(get_session),
) -> list[ApplicationServiceBindingOut]:
    rows = (
        await session.execute(
            select(ApplicationServiceBinding, Service)
            .join(Service, Service.id == ApplicationServiceBinding.service_id)
            .where(ApplicationServiceBinding.application_id == application_id)
            .order_by(ApplicationServiceBinding.role, Service.service_name)
        )
    ).all()
    return [
        ApplicationServiceBindingOut(
            application_id=application_id,
            id=service.id,
            service_name=service.service_name,
            repo_id=service.repo_id,
            state=service.state,
            role=binding.role,
        )
        for binding, service in rows
    ]


@router.post(
    "/{application_id}/services",
    response_model=ApplicationServiceBindingOut,
    status_code=201,
)
async def bind_application_service(
    application_id: int,
    payload: ApplicationServiceBindingIn,
    actor_id: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> ApplicationServiceBindingOut:
    if await session.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="application not found")
    service = await session.get(Service, payload.service_id)
    if service is None or service.state != "active":
        raise HTTPException(status_code=404, detail="active service not found")
    binding = ApplicationServiceBinding(
        application_id=application_id,
        service_id=service.id,
        role=payload.role,
    )
    session.add(binding)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="service binding conflicts") from exc
    await audit_action(
        action="application.service.bind",
        actor_id=actor_id,
        target_type="service",
        target_id=str(service.id),
        application_id=application_id,
        detail={"role": payload.role},
    )
    return ApplicationServiceBindingOut(
        application_id=application_id,
        id=service.id,
        service_name=service.service_name,
        repo_id=service.repo_id,
        state=service.state,
        role=payload.role,
    )


@router.delete("/{application_id}/services/{service_id}", status_code=204)
async def unbind_application_service(
    application_id: int,
    service_id: int,
    actor_id: int = Security(require_app_perm, scopes=["admin"]),
    session: AsyncSession = Depends(get_session),
) -> None:
    binding = await session.get(ApplicationServiceBinding, (application_id, service_id))
    if binding is None:
        raise HTTPException(status_code=404, detail="service binding not found")
    await session.delete(binding)
    await session.commit()
    await audit_action(
        action="application.service.unbind",
        actor_id=actor_id,
        target_type="service",
        target_id=str(service_id),
        application_id=application_id,
    )
