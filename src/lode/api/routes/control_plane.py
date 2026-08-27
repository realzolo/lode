"""Final provider, Workspace, repository, and connector control plane."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from aiokafka.admin import AIOKafkaAdminClient
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.control_schemas import (
    ConnectorCreate,
    ConnectorOut,
    ConnectorPatch,
    IngestionStart,
    LocalRepositoryCreate,
    ModelBindingInput,
    ModelBindingOut,
    ModelBindingPatch,
    ModelDeploymentCreate,
    ModelDeploymentOut,
    ModelDeploymentPatch,
    ModelPolicyInput,
    ModelPolicyOut,
    ProviderAccountCreate,
    ProviderAccountOut,
    ProviderAccountPatch,
    RepositoryBind,
    RepositoryBindingOut,
    RepositoryBindingPatch,
    WorkspaceCreate,
    WorkspaceOut,
)
from lode.api.deps import assert_workspace_permission, require_admin, require_user
from lode.config import kafka_security_kwargs, settings
from lode.crypto import decrypt_secret, encrypt_secret
from lode.db.models import (
    AIProviderAccount,
    AuditEvent,
    ContextPolicyRevision,
    EvidenceAccessScope,
    EvidenceConnector,
    GitCredential,
    GitRepository,
    Investigation,
    ModelDeployment,
    ModelPolicyRevision,
    ModelRoutingDecision,
    ProviderModelObservation,
    User,
    Workspace,
    WorkspaceModelBinding,
    WorkspacePermission,
    WorkspaceRepositoryBinding,
)
from lode.db.session import AsyncSessionLocal
from lode.domain.investigation import canonical_hash
from lode.engine.llm import ModelConfig, complete_with_usage
from lode.evidence_connectors.registry import (
    create_evidence_connector,
    native_connector_capabilities,
)
from lode.evidence_connectors.types import IntrospectionBudget, ProviderExecutionError
from lode.infrastructure.git_source import validate_git_remote
from lode.infrastructure.provider_http import (
    provider_endpoint,
    provider_request,
    validate_provider_endpoint,
)

router = APIRouter(tags=["control-plane"])
_REQUIRED_MODEL_ROLES = {
    "planner",
    "native_query",
    "synthesizer",
    "verifier",
    "context_compactor",
}


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def _error(status: int, code: str, message: str, **details) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message, **details},
    )


async def _active_user(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise _error(401, "active_user_required", "An active user is required.")
    return user


async def _workspace_access(
    session: AsyncSession, user_id: int, workspace_id: int, permission: str
) -> tuple[User, Workspace]:
    user = await _active_user(session, user_id)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise _error(404, "workspace_not_found", "Workspace not found.")
    await assert_workspace_permission(session, user, workspace_id, permission)
    return user, workspace


def _validate_provider_url(value: str) -> str:
    try:
        return validate_provider_endpoint(value)
    except ValueError as exc:
        raise _error(
            422,
            "invalid_provider_endpoint",
            "Provider endpoints must be credential-free HTTPS URLs.",
        ) from exc


def _provider_out(row: AIProviderAccount) -> ProviderAccountOut:
    return ProviderAccountOut.model_validate(row)


def _deployment_out(row: ModelDeployment) -> ModelDeploymentOut:
    return ModelDeploymentOut.model_validate(row)


def _binding_out(row: WorkspaceModelBinding) -> ModelBindingOut:
    return ModelBindingOut.model_validate(row)


def _audit(user: User, action: str, target_type: str, target_id: int, workspace_id=None):
    return AuditEvent(
        actor_id=user.id,
        actor_email=user.email,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        workspace_id=workspace_id,
        result="ok",
        detail={},
    )


@router.get("/ai-provider-accounts", response_model=list[ProviderAccountOut])
async def list_provider_accounts(
    _: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(select(AIProviderAccount).order_by(AIProviderAccount.id))
    ).scalars()
    return [_provider_out(row) for row in rows]


@router.post("/ai-provider-accounts", response_model=ProviderAccountOut, status_code=201)
async def create_provider_account(
    payload: ProviderAccountCreate,
    user_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = AIProviderAccount(
        name=payload.name.strip(),
        provider_kind=payload.provider_kind,
        base_url=_validate_provider_url(payload.base_url),
        credential_ciphertext=encrypt_secret(payload.credential) or "",
        organization_ref=payload.organization_ref,
        project_ref=payload.project_ref,
        tenant_ref=payload.tenant_ref,
        rate_limit_policy=payload.rate_limit_policy,
        cost_policy=payload.cost_policy,
        data_processing_policy_revision=payload.data_processing_policy_revision,
        data_residency=payload.data_residency,
        retention_mode=payload.retention_mode,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(
            409, "provider_name_conflict", "Provider account name is already used."
        ) from exc
    session.add(_audit(user, "provider_account.create", "ai_provider_account", row.id))
    await session.commit()
    await session.refresh(row)
    return _provider_out(row)


@router.patch("/ai-provider-accounts/{account_id}", response_model=ProviderAccountOut)
async def patch_provider_account(
    account_id: int,
    payload: ProviderAccountPatch,
    user_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = await session.get(AIProviderAccount, account_id)
    if row is None:
        raise _error(404, "provider_account_not_found", "Provider account not found.")
    values = payload.model_dump(exclude_unset=True)
    credential = values.pop("credential", None)
    if "base_url" in values:
        values["base_url"] = _validate_provider_url(values["base_url"])
    health_changed = (
        bool(
            {"base_url", "data_processing_policy_revision", "data_residency", "retention_mode"}
            & values.keys()
        )
        or credential is not None
    )
    for key, value in values.items():
        setattr(row, key, value)
    if credential is not None:
        row.credential_ciphertext = encrypt_secret(credential) or ""
    row.revision += 1
    if health_changed:
        row.verification_status = "untested"
        row.verified_at = None
    session.add(_audit(user, "provider_account.update", "ai_provider_account", row.id))
    await session.commit()
    await session.refresh(row)
    return _provider_out(row)


async def _provider_models(row: AIProviderAccount) -> list[dict]:
    credential = decrypt_secret(row.credential_ciphertext)
    if not credential:
        raise _error(422, "provider_credential_unavailable", "Provider credential is unavailable.")
    headers = {"accept": "application/json"}
    if row.provider_kind == "anthropic":
        headers.update({"x-api-key": credential, "anthropic-version": "2023-06-01"})
    else:
        headers["authorization"] = f"Bearer {credential}"
        if row.organization_ref:
            headers["OpenAI-Organization"] = row.organization_ref
        if row.project_ref:
            headers["OpenAI-Project"] = row.project_ref
    try:
        response = await provider_request(
            "GET",
            provider_endpoint(row.base_url, "/models"),
            headers=headers,
            timeout_seconds=settings.llm_probe_timeout_seconds,
        )
    except (ValueError, ProviderExecutionError) as exc:
        raise _error(
            502, "provider_probe_failed", "Provider model inventory request failed."
        ) from exc
    if response.status_code >= 400:
        raise _error(502, "provider_probe_failed", "Provider model inventory request failed.")
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _error(
            502, "provider_protocol_invalid", "Provider model inventory is invalid."
        ) from exc
    models = value.get("data") if isinstance(value, dict) else None
    if not isinstance(models, list) or len(models) > 200:
        raise _error(502, "provider_protocol_invalid", "Provider model inventory is invalid.")
    output = []
    for item in models:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            output.append({"id": item["id"], "owned_by": item.get("owned_by")})
    if not output:
        raise _error(502, "provider_protocol_invalid", "Provider returned no usable models.")
    return output


@router.post("/ai-provider-accounts/{account_id}/test")
async def test_provider_account(
    account_id: int,
    _: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AIProviderAccount, account_id)
    if row is None:
        raise _error(404, "provider_account_not_found", "Provider account not found.")
    try:
        models = await _provider_models(row)
    except HTTPException:
        row.verification_status = "unavailable"
        row.verified_at = None
        await session.commit()
        raise
    row.verification_status = "healthy"
    row.verified_at = datetime.now(UTC)
    await session.commit()
    return {"available": True, "model_count": len(models), "verified_at": row.verified_at}


@router.post("/ai-provider-accounts/{account_id}/introspect-models")
async def introspect_provider_models(
    account_id: int,
    _: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AIProviderAccount, account_id)
    if row is None:
        raise _error(404, "provider_account_not_found", "Provider account not found.")
    models = await _provider_models(row)
    observed_at = datetime.now(UTC)
    for model in models:
        payload = {"provider_model_id": model["id"], "owned_by": model.get("owned_by")}
        response_hash = canonical_hash(payload)
        existing = await session.scalar(
            select(ProviderModelObservation.id).where(
                ProviderModelObservation.provider_account_id == row.id,
                ProviderModelObservation.provider_model_id == model["id"],
                ProviderModelObservation.response_hash == response_hash,
            )
        )
        if existing is None:
            session.add(
                ProviderModelObservation(
                    provider_account_id=row.id,
                    provider_model_id=model["id"],
                    capability_hints={},
                    provider_payload_masked=payload,
                    observed_at=observed_at,
                    response_hash=response_hash,
                )
            )
    await session.commit()
    return {"observed_at": observed_at, "models": models}


@router.delete("/ai-provider-accounts/{account_id}", status_code=204)
async def disable_provider_account(
    account_id: int,
    user_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = await session.get(AIProviderAccount, account_id)
    if row is None:
        raise _error(404, "provider_account_not_found", "Provider account not found.")
    row.state = "disabled"
    row.revision += 1
    session.add(_audit(user, "provider_account.disable", "ai_provider_account", row.id))
    await session.commit()
    return Response(status_code=204)


@router.get("/ai-model-deployments", response_model=list[ModelDeploymentOut])
async def list_model_deployments(
    _: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    rows = (await session.execute(select(ModelDeployment).order_by(ModelDeployment.id))).scalars()
    return [_deployment_out(row) for row in rows]


@router.post(
    "/ai-provider-accounts/{account_id}/model-deployments",
    response_model=ModelDeploymentOut,
    status_code=201,
)
async def create_model_deployment(
    account_id: int,
    payload: ModelDeploymentCreate,
    user_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    provider = await session.get(AIProviderAccount, account_id)
    if provider is None or provider.state != "active":
        raise _error(404, "active_provider_not_found", "Active provider account not found.")
    row = ModelDeployment(provider_account_id=account_id, **payload.model_dump())
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "model_deployment_conflict", "Model deployment already exists.") from exc
    session.add(_audit(user, "model_deployment.create", "model_deployment", row.id))
    await session.commit()
    await session.refresh(row)
    return _deployment_out(row)


@router.patch("/ai-model-deployments/{deployment_id}", response_model=ModelDeploymentOut)
async def patch_model_deployment(
    deployment_id: int,
    payload: ModelDeploymentPatch,
    user_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = await session.get(ModelDeployment, deployment_id)
    if row is None:
        raise _error(404, "model_deployment_not_found", "Model deployment not found.")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(row, key, value)
    row.revision += 1
    if set(values) - {"display_name", "state"}:
        row.availability_state = "untested"
        row.health_checked_at = None
    session.add(_audit(user, "model_deployment.update", "model_deployment", row.id))
    await session.commit()
    await session.refresh(row)
    return _deployment_out(row)


@router.post("/ai-model-deployments/{deployment_id}/test")
async def test_model_deployment(
    deployment_id: int,
    _: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(ModelDeployment, deployment_id)
    provider = await session.get(AIProviderAccount, row.provider_account_id) if row else None
    if row is None or provider is None:
        raise _error(404, "model_deployment_not_found", "Model deployment not found.")
    result = await complete_with_usage(
        "You are a protocol health probe.",
        "Reply with OK.",
        ModelConfig(
            provider=provider.provider_kind,
            base_url=provider.base_url,
            api_key_ciphertext=provider.credential_ciphertext,
            model=row.provider_model_id,
        ),
        timeout_seconds=settings.llm_probe_timeout_seconds,
    )
    row.availability_state = "healthy" if result.text else "unavailable"
    row.health_checked_at = datetime.now(UTC)
    await session.commit()
    return {
        "available": bool(result.text),
        "latency_ms": result.latency_ms,
        "error_code": result.error_code,
    }


@router.delete("/ai-model-deployments/{deployment_id}", status_code=204)
async def disable_model_deployment(
    deployment_id: int,
    user_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = await session.get(ModelDeployment, deployment_id)
    if row is None:
        raise _error(404, "model_deployment_not_found", "Model deployment not found.")
    row.state = "disabled"
    row.revision += 1
    session.add(_audit(user, "model_deployment.disable", "model_deployment", row.id))
    await session.commit()
    return Response(status_code=204)


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    user_id: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = Workspace(
        name=payload.name,
        ingestion_topic=payload.ingestion_topic,
        created_by=user.id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(
            409, "workspace_topic_conflict", "Ingestion topic is already assigned."
        ) from exc
    session.add(WorkspacePermission(user_id=user.id, workspace_id=row.id, permission="admin"))
    session.add(_audit(user, "workspace.create", "workspace", row.id, row.id))
    await session.commit()
    await session.refresh(row)
    return WorkspaceOut.model_validate(row)


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(
    user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)
):
    user = await _active_user(session, user_id)
    statement = select(Workspace).order_by(Workspace.name, Workspace.id)
    if user.role != "admin":
        statement = statement.join(WorkspacePermission).where(
            WorkspacePermission.user_id == user.id
        )
    rows = (await session.execute(statement)).scalars().unique()
    return [WorkspaceOut.model_validate(row) for row in rows]


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, row = await _workspace_access(session, user_id, workspace_id, "read")
    return WorkspaceOut.model_validate(row)


async def _broker_has_topic(topic: str) -> bool:
    client = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        **kafka_security_kwargs(),
    )
    await client.start()
    try:
        metadata = await client.describe_topics([topic])
        return bool(metadata and not metadata[0].get("error_code"))
    finally:
        await client.close()


async def _workspace_readiness(session: AsyncSession, workspace: Workspace) -> list[str]:
    missing: list[str] = []
    if not workspace.ingestion_topic.strip():
        missing.append("topic")
    policy = await session.get(ModelPolicyRevision, workspace.model_policy_revision_id)
    if policy is None:
        missing.append("model_policy")
    else:
        expected_revisions = {
            int(item["binding_id"]): int(item["revision"]) for item in policy.eligible_bindings
        }
        binding_ids = list(expected_revisions)
        bindings = tuple(
            (
                await session.execute(
                    select(WorkspaceModelBinding, ModelDeployment, AIProviderAccount)
                    .join(
                        ModelDeployment,
                        ModelDeployment.id == WorkspaceModelBinding.model_deployment_id,
                    )
                    .join(
                        AIProviderAccount,
                        AIProviderAccount.id == ModelDeployment.provider_account_id,
                    )
                    .where(WorkspaceModelBinding.id.in_(binding_ids))
                )
            ).all()
        )
        roles = {
            role
            for binding, deployment, provider in bindings
            if binding.state == "active"
            and binding.revision == expected_revisions.get(binding.id)
            and deployment.state == "active"
            and deployment.availability_state == "healthy"
            and provider.state == "active"
            and provider.verification_status == "healthy"
            for role in binding.allowed_roles
        }
        if not _REQUIRED_MODEL_ROLES.issubset(roles):
            missing.append("model_roles")
    try:
        reachable = await asyncio.wait_for(
            _broker_has_topic(workspace.ingestion_topic),
            timeout=settings.kafka_topic_validation_timeout_seconds,
        )
    except Exception:
        reachable = False
    if not reachable:
        missing.append("broker_reachability")
    return missing


async def _set_ingestion(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    target: str,
    start_position: str | None = None,
):
    if target == "active":
        missing = await _workspace_readiness(session, workspace)
        if missing:
            raise _error(
                409,
                "workspace_not_ready",
                "Complete all required Workspace settings before starting ingestion.",
                missing=missing,
            )
        workspace.ingestion_version += 1
        workspace.ingestion_start_position = start_position or workspace.ingestion_start_position
        workspace.ingestion_started_at = datetime.now(UTC)
        workspace.ingestion_paused_at = None
    else:
        workspace.ingestion_paused_at = datetime.now(UTC)
    workspace.ingestion_state = target
    session.add(
        _audit(user, f"workspace.ingestion.{target}", "workspace", workspace.id, workspace.id)
    )
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceOut.model_validate(workspace)


@router.post("/workspaces/{workspace_id}/ingestion/start", response_model=WorkspaceOut)
async def start_ingestion(
    workspace_id: int,
    payload: IngestionStart,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    if workspace.ingestion_state != "draft":
        raise _error(409, "ingestion_transition_invalid", "Only draft ingestion can start.")
    return await _set_ingestion(session, user, workspace, "active", payload.start_position)


@router.post("/workspaces/{workspace_id}/ingestion/pause", response_model=WorkspaceOut)
async def pause_ingestion(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    if workspace.ingestion_state != "active":
        raise _error(409, "ingestion_transition_invalid", "Only active ingestion can pause.")
    return await _set_ingestion(session, user, workspace, "paused")


@router.post("/workspaces/{workspace_id}/ingestion/resume", response_model=WorkspaceOut)
async def resume_ingestion(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    if workspace.ingestion_state != "paused":
        raise _error(409, "ingestion_transition_invalid", "Only paused ingestion can resume.")
    return await _set_ingestion(session, user, workspace, "active")


@router.get("/workspaces/{workspace_id}/model-bindings", response_model=list[ModelBindingOut])
async def list_model_bindings(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    rows = (
        await session.execute(
            select(WorkspaceModelBinding)
            .where(WorkspaceModelBinding.workspace_id == workspace_id)
            .order_by(WorkspaceModelBinding.priority, WorkspaceModelBinding.id)
        )
    ).scalars()
    return [_binding_out(row) for row in rows]


@router.post(
    "/workspaces/{workspace_id}/model-bindings",
    response_model=ModelBindingOut,
    status_code=201,
)
async def create_model_binding(
    workspace_id: int,
    payload: ModelBindingInput,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    deployment = await session.get(ModelDeployment, payload.model_deployment_id)
    if deployment is None or deployment.state != "active":
        raise _error(422, "model_deployment_ineligible", "Active model deployment required.")
    row = WorkspaceModelBinding(
        workspace_id=workspace_id,
        **payload.model_dump(mode="json"),
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "active_model_binding_conflict", "Deployment is already bound.") from exc
    session.add(
        _audit(user, "model_binding.create", "workspace_model_binding", row.id, workspace_id)
    )
    await session.commit()
    await session.refresh(row)
    return _binding_out(row)


@router.patch(
    "/workspaces/{workspace_id}/model-bindings/{binding_id}",
    response_model=ModelBindingOut,
)
async def patch_model_binding(
    workspace_id: int,
    binding_id: int,
    payload: ModelBindingPatch,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(WorkspaceModelBinding, binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "model_binding_not_found", "Model binding not found.")
    values = payload.model_dump(exclude_unset=True, mode="json")
    state = values.pop("state", None)
    effective = ModelBindingInput(
        model_deployment_id=row.model_deployment_id,
        execution_classes=values.get("execution_classes", row.execution_classes),
        allowed_roles=values.get("allowed_roles", row.allowed_roles),
        priority=values.get("priority", row.priority),
        max_calls=values.get("max_calls", row.max_calls),
        max_input_tokens=values.get("max_input_tokens", row.max_input_tokens),
        max_output_tokens=values.get("max_output_tokens", row.max_output_tokens),
        max_cost_per_call=values.get("max_cost_per_call", row.max_cost_per_call),
        timeout_ms=values.get("timeout_ms", row.timeout_ms),
        allowed_data_classes=values.get("allowed_data_classes", row.allowed_data_classes),
        max_context_utilization=values.get("max_context_utilization", row.max_context_utilization),
    ).model_dump(mode="json")
    effective.pop("model_deployment_id")
    for key, value in effective.items():
        setattr(row, key, value)
    if state is not None:
        row.state = state
    row.revision += 1
    session.add(
        _audit(user, "model_binding.update", "workspace_model_binding", row.id, workspace_id)
    )
    await session.commit()
    await session.refresh(row)
    return _binding_out(row)


@router.delete("/workspaces/{workspace_id}/model-bindings/{binding_id}", status_code=204)
async def disable_model_binding(
    workspace_id: int,
    binding_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(WorkspaceModelBinding, binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "model_binding_not_found", "Model binding not found.")
    row.state = "disabled"
    row.revision += 1
    session.add(
        _audit(user, "model_binding.disable", "workspace_model_binding", row.id, workspace_id)
    )
    await session.commit()
    return Response(status_code=204)


@router.put("/workspaces/{workspace_id}/model-policy", response_model=ModelPolicyOut)
async def put_model_policy(
    workspace_id: int,
    payload: ModelPolicyInput,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    bindings = tuple(
        (
            await session.execute(
                select(WorkspaceModelBinding).where(
                    WorkspaceModelBinding.workspace_id == workspace_id,
                    WorkspaceModelBinding.id.in_(payload.eligible_binding_ids),
                    WorkspaceModelBinding.state == "active",
                )
            )
        ).scalars()
    )
    if len(bindings) != len(set(payload.eligible_binding_ids)):
        raise _error(
            422,
            "model_policy_binding_invalid",
            "Every policy binding must be active and owned by the Workspace.",
        )
    eligible_roles = {role for binding in bindings for role in binding.allowed_roles}
    if not _REQUIRED_MODEL_ROLES.issubset(eligible_roles):
        raise _error(
            422,
            "model_policy_roles_incomplete",
            "Eligible bindings must cover every required model role.",
            missing=sorted(_REQUIRED_MODEL_ROLES - eligible_roles),
        )
    configured_roles = set(payload.role_policies)
    if not _REQUIRED_MODEL_ROLES.issubset(configured_roles):
        raise _error(
            422,
            "model_policy_role_config_incomplete",
            "Role policies must configure every required model role.",
            missing=sorted(_REQUIRED_MODEL_ROLES - configured_roles),
        )
    policy_revision = (
        int(
            (
                await session.scalar(
                    select(func.coalesce(func.max(ModelPolicyRevision.revision), 0)).where(
                        ModelPolicyRevision.workspace_id == workspace_id
                    )
                )
            )
            or 0
        )
        + 1
    )
    context_revision = (
        int(
            (
                await session.scalar(
                    select(func.coalesce(func.max(ContextPolicyRevision.revision), 0)).where(
                        ContextPolicyRevision.workspace_id == workspace_id
                    )
                )
            )
            or 0
        )
        + 1
    )
    context = ContextPolicyRevision(
        workspace_id=workspace_id,
        pinned_evidence_kinds=list(payload.pinned_evidence_kinds),
        compression_levels=list(payload.compression_levels),
        minimum_output_tokens=payload.minimum_output_tokens,
        provider_safety_margin_tokens=payload.provider_safety_margin_tokens,
        revision=context_revision,
    )
    session.add(context)
    await session.flush()
    policy = ModelPolicyRevision(
        workspace_id=workspace_id,
        eligible_bindings=[
            {"binding_id": row.id, "revision": row.revision}
            for row in sorted(bindings, key=lambda item: item.id)
        ],
        role_policies=payload.role_policies,
        budget_policy=payload.budget_policy,
        context_policy_revision_id=context.id,
        verifier_policy=payload.verifier_policy,
        revision=policy_revision,
    )
    session.add(policy)
    await session.flush()
    workspace.model_policy_revision_id = policy.id
    session.add(
        _audit(user, "model_policy.publish", "model_policy_revision", policy.id, workspace_id)
    )
    await session.commit()
    await session.refresh(policy)
    return ModelPolicyOut.model_validate(policy)


@router.get("/workspaces/{workspace_id}/model-routing-audit")
async def model_routing_audit(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    rows = (
        await session.execute(
            select(ModelRoutingDecision)
            .join(Investigation, Investigation.id == ModelRoutingDecision.investigation_id)
            .where(Investigation.workspace_id == workspace_id)
            .order_by(ModelRoutingDecision.id.desc())
            .limit(200)
        )
    ).scalars()
    return [
        {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "role": row.role,
            "execution_class": row.execution_class,
            "model_binding_snapshot_id": row.model_binding_snapshot_id,
            "selection_reason": row.selection_reason,
            "excluded_candidates": row.excluded_candidates,
            "budget": row.budget,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/workspaces/{workspace_id}/capabilities")
async def workspace_capabilities(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, workspace = await _workspace_access(session, user_id, workspace_id, "read")
    model_count = int(
        await session.scalar(
            select(func.count())
            .select_from(WorkspaceModelBinding)
            .where(
                WorkspaceModelBinding.workspace_id == workspace_id,
                WorkspaceModelBinding.state == "active",
            )
        )
        or 0
    )
    repository_count = int(
        await session.scalar(
            select(func.count())
            .select_from(WorkspaceRepositoryBinding)
            .where(
                WorkspaceRepositoryBinding.workspace_id == workspace_id,
                WorkspaceRepositoryBinding.state == "active",
            )
        )
        or 0
    )
    connector_count = int(
        await session.scalar(
            select(func.count())
            .select_from(EvidenceConnector)
            .where(
                EvidenceConnector.workspace_id == workspace_id,
                EvidenceConnector.state == "active",
                EvidenceConnector.verification_status == "healthy",
            )
        )
        or 0
    )
    gaps = []
    if workspace.model_policy_revision_id is None or model_count == 0:
        gaps.append("model_policy")
    if repository_count == 0:
        gaps.append("repositories")
    if connector_count == 0:
        gaps.append("evidence_connectors")
    return {
        "workspace_id": workspace_id,
        "models": model_count,
        "repositories": repository_count,
        "healthy_connectors": connector_count,
        "gaps": gaps,
    }


def _repository_out(binding: WorkspaceRepositoryBinding, repository: GitRepository):
    return RepositoryBindingOut(
        id=binding.id,
        workspace_id=binding.workspace_id,
        repository_id=repository.id,
        name=repository.name,
        repo_url=repository.repo_url,
        repo_type=repository.repo_type,
        default_branch=repository.default_branch,
        role=binding.role,
        priority=binding.priority,
        description=binding.description,
        state=binding.state,
        revision=binding.revision,
    )


@router.get("/workspaces/{workspace_id}/repositories", response_model=list[RepositoryBindingOut])
async def list_repositories(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    rows = (
        await session.execute(
            select(WorkspaceRepositoryBinding, GitRepository)
            .join(GitRepository, GitRepository.id == WorkspaceRepositoryBinding.repository_id)
            .where(WorkspaceRepositoryBinding.workspace_id == workspace_id)
            .order_by(WorkspaceRepositoryBinding.priority, WorkspaceRepositoryBinding.id)
        )
    ).all()
    return [_repository_out(binding, repository) for binding, repository in rows]


async def _create_repository_binding(session, workspace_id, user, repository, payload):
    row = WorkspaceRepositoryBinding(
        workspace_id=workspace_id,
        repository_id=repository.id,
        role=payload.role,
        priority=payload.priority,
        description=payload.description,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "repository_binding_conflict", "Repository is already bound.") from exc
    session.add(
        _audit(user, "repository.bind", "workspace_repository_binding", row.id, workspace_id)
    )
    await session.commit()
    await session.refresh(row)
    return _repository_out(row, repository)


@router.post(
    "/workspaces/{workspace_id}/repositories", response_model=RepositoryBindingOut, status_code=201
)
async def bind_repository(
    workspace_id: int,
    payload: RepositoryBind,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    repository = await session.get(GitRepository, payload.repository_id)
    if repository is None or (
        repository.scope == "workspace" and repository.workspace_id != workspace_id
    ):
        raise _error(404, "repository_not_found", "Repository not found in allowed scope.")
    return await _create_repository_binding(session, workspace_id, user, repository, payload)


@router.post(
    "/workspaces/{workspace_id}/repositories/local",
    response_model=RepositoryBindingOut,
    status_code=201,
)
async def create_local_repository(
    workspace_id: int,
    payload: LocalRepositoryCreate,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    if payload.credential_id is not None:
        credential = await session.get(GitCredential, payload.credential_id)
        if credential is None or not credential.readonly:
            raise _error(422, "git_credential_invalid", "A read-only Git credential is required.")
    try:
        validate_git_remote(payload.repo_url)
    except ValueError as exc:
        raise _error(422, "git_repository_url_invalid", str(exc)) from exc
    repository = GitRepository(
        name=payload.name,
        repo_url=payload.repo_url,
        repo_type=payload.repo_type,
        default_branch=payload.default_branch,
        credential_id=payload.credential_id,
        scope="workspace",
        workspace_id=workspace_id,
    )
    session.add(repository)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(
            409, "repository_url_conflict", "Repository URL is already registered."
        ) from exc
    return await _create_repository_binding(session, workspace_id, user, repository, payload)


@router.patch(
    "/workspaces/{workspace_id}/repositories/{binding_id}", response_model=RepositoryBindingOut
)
async def patch_repository_binding(
    workspace_id: int,
    binding_id: int,
    payload: RepositoryBindingPatch,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(WorkspaceRepositoryBinding, binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "repository_binding_not_found", "Repository binding not found.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.revision += 1
    repository = await session.get(GitRepository, row.repository_id)
    session.add(
        _audit(
            user, "repository_binding.update", "workspace_repository_binding", row.id, workspace_id
        )
    )
    await session.commit()
    await session.refresh(row)
    assert repository is not None
    return _repository_out(row, repository)


@router.delete("/workspaces/{workspace_id}/repositories/{binding_id}", status_code=204)
async def disable_repository_binding(
    workspace_id: int,
    binding_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(WorkspaceRepositoryBinding, binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "repository_binding_not_found", "Repository binding not found.")
    row.state = "disabled"
    row.revision += 1
    session.add(
        _audit(
            user, "repository_binding.disable", "workspace_repository_binding", row.id, workspace_id
        )
    )
    await session.commit()
    return Response(status_code=204)


_CONNECTOR_SECRET_FIELDS = {
    "loki": ["bearer_token"],
    "elasticsearch": ["api_key", "bearer_token", "username", "password"],
    "opensearch": ["api_key", "bearer_token", "username", "password"],
    "postgresql": ["password"],
    "mysql": ["password"],
    "https": ["bearer_token", "api_key"],
    "command_runner": ["runner_key"],
}


def _connector_secret_map(row: EvidenceConnector) -> dict[str, str]:
    plaintext = decrypt_secret(row.secret_ciphertext)
    try:
        value = json.loads(plaintext or "", object_pairs_hook=_unique_pairs)
    except (json.JSONDecodeError, DuplicateKey) as exc:
        raise _error(
            500, "connector_secret_invalid", "Stored connector secret is invalid."
        ) from exc
    if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
        raise _error(500, "connector_secret_invalid", "Stored connector secret is invalid.")
    return value


class DuplicateKey(ValueError):
    pass


def _unique_pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def _connector_out(row: EvidenceConnector) -> ConnectorOut:
    return ConnectorOut(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        kind=row.kind,
        kind_version=row.kind_version,
        config=row.config,
        instance_revision=row.instance_revision,
        state=row.state,
        verification_status=row.verification_status,
        verified_at=row.verified_at,
        last_error=row.last_error,
        capabilities=row.capabilities,
        last_introspected_at=row.last_introspected_at,
        configured_secret_fields=sorted(_connector_secret_map(row)),
    )


@router.get("/evidence-connector-kinds")
async def connector_kinds(_: int = Depends(require_user)):
    capabilities = native_connector_capabilities()
    return [
        {
            "kind": kind,
            "version": 1,
            "language": value["language"],
            "capabilities": list(value["read_capabilities"]),
            "secret_fields": _CONNECTOR_SECRET_FIELDS[kind],
        }
        for kind, value in sorted(capabilities.items())
    ]


@router.get("/workspaces/{workspace_id}/evidence-connectors", response_model=list[ConnectorOut])
async def list_connectors(
    workspace_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    rows = (
        await session.execute(
            select(EvidenceConnector)
            .where(EvidenceConnector.workspace_id == workspace_id)
            .order_by(EvidenceConnector.name, EvidenceConnector.id)
        )
    ).scalars()
    return [_connector_out(row) for row in rows]


def _connector_capability(kind: str):
    value = native_connector_capabilities()[kind]
    language = value["language"]
    return getattr(language, "value", str(language)), list(value["read_capabilities"])


@router.post(
    "/workspaces/{workspace_id}/evidence-connectors", response_model=ConnectorOut, status_code=201
)
async def create_connector(
    workspace_id: int,
    payload: ConnectorCreate,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    try:
        create_evidence_connector(payload.kind, payload.config, payload.secrets)
    except ValueError as exc:
        raise _error(422, "connector_configuration_invalid", str(exc)) from exc
    language, capabilities = _connector_capability(payload.kind)
    ciphertext = (
        encrypt_secret(
            json.dumps(payload.secrets, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        or ""
    )
    row = EvidenceConnector(
        workspace_id=workspace_id,
        name=payload.name,
        kind=payload.kind,
        kind_version=1,
        config=payload.config,
        secret_ciphertext=ciphertext,
        instance_revision=1,
        capabilities=capabilities,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "connector_name_conflict", "Connector name is already used.") from exc
    session.add(
        EvidenceAccessScope(
            connector_id=row.id,
            allowed_languages=[language],
            scope_config=payload.scope_config,
            schema_catalog=payload.schema_catalog,
            schema_catalog_revision=1,
            read_policy_revision=1,
            execution_budget_policy=payload.execution_budget_policy,
            normalization_policy_revision=1,
            revision=1,
        )
    )
    session.add(
        _audit(user, "evidence_connector.create", "evidence_connector", row.id, workspace_id)
    )
    await session.commit()
    await session.refresh(row)
    return _connector_out(row)


async def _latest_scope(session: AsyncSession, connector_id: int) -> EvidenceAccessScope:
    row = (
        await session.execute(
            select(EvidenceAccessScope)
            .where(EvidenceAccessScope.connector_id == connector_id)
            .order_by(EvidenceAccessScope.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise _error(409, "connector_scope_missing", "Connector access scope is missing.")
    return row


@router.patch(
    "/workspaces/{workspace_id}/evidence-connectors/{connector_id}", response_model=ConnectorOut
)
async def patch_connector(
    workspace_id: int,
    connector_id: int,
    payload: ConnectorPatch,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(EvidenceConnector, connector_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "connector_not_found", "Evidence connector not found.")
    scope = await _latest_scope(session, connector_id)
    values = payload.model_dump(exclude_unset=True)
    secrets = values.pop("secrets", None)
    config = values.pop("config", None)
    scope_config = values.pop("scope_config", None)
    schema_catalog = values.pop("schema_catalog", None)
    budget = values.pop("execution_budget_policy", None)
    effective_config = config if config is not None else row.config
    effective_secrets = secrets if secrets is not None else _connector_secret_map(row)
    try:
        create_evidence_connector(row.kind, effective_config, effective_secrets)
    except ValueError as exc:
        raise _error(422, "connector_configuration_invalid", str(exc)) from exc
    for key, value in values.items():
        setattr(row, key, value)
    row.config = effective_config
    if secrets is not None:
        row.secret_ciphertext = (
            encrypt_secret(json.dumps(secrets, separators=(",", ":"), sort_keys=True)) or ""
        )
    row.instance_revision += 1
    row.verification_status = "untested"
    row.verified_at = None
    row.last_error = None
    if any(value is not None for value in (scope_config, schema_catalog, budget)):
        session.add(
            EvidenceAccessScope(
                connector_id=row.id,
                allowed_languages=scope.allowed_languages,
                scope_config=scope_config if scope_config is not None else scope.scope_config,
                schema_catalog=schema_catalog
                if schema_catalog is not None
                else scope.schema_catalog,
                schema_catalog_revision=scope.schema_catalog_revision + 1,
                read_policy_revision=scope.read_policy_revision,
                execution_budget_policy=budget
                if budget is not None
                else scope.execution_budget_policy,
                normalization_policy_revision=scope.normalization_policy_revision,
                revision=scope.revision + 1,
            )
        )
    session.add(
        _audit(user, "evidence_connector.update", "evidence_connector", row.id, workspace_id)
    )
    await session.commit()
    await session.refresh(row)
    return _connector_out(row)


@router.post("/workspaces/{workspace_id}/evidence-connectors/{connector_id}/test")
async def test_connector(
    workspace_id: int,
    connector_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(EvidenceConnector, connector_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "connector_not_found", "Evidence connector not found.")
    adapter = create_evidence_connector(row.kind, row.config, _connector_secret_map(row))
    try:
        verification = await adapter.verify()
    except Exception as exc:
        row.verification_status = "unavailable"
        row.verified_at = None
        row.last_error = type(exc).__name__
        await session.commit()
        raise _error(
            502, "connector_verification_failed", "Read-only connector verification failed."
        ) from exc
    row.verification_status = "healthy"
    row.verified_at = datetime.now(UTC)
    row.last_error = None
    await session.commit()
    return {
        "provider": verification.provider,
        "version": verification.version,
        "capabilities": list(verification.capabilities),
        "verified_at": row.verified_at,
    }


@router.post("/workspaces/{workspace_id}/evidence-connectors/{connector_id}/introspect")
async def introspect_connector(
    workspace_id: int,
    connector_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(EvidenceConnector, connector_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "connector_not_found", "Evidence connector not found.")
    if row.verification_status != "healthy":
        raise _error(409, "connector_not_verified", "Verify the connector before introspection.")
    scope = await _latest_scope(session, connector_id)
    adapter = create_evidence_connector(row.kind, row.config, _connector_secret_map(row))
    now = datetime.now(UTC)
    catalog = await adapter.introspect(
        scope.scope_config,
        IntrospectionBudget(
            timeout_ms=5_000,
            max_resources=500,
            window_start=now - timedelta(minutes=30),
            window_end=now,
        ),
    )
    new_scope = EvidenceAccessScope(
        connector_id=row.id,
        allowed_languages=scope.allowed_languages,
        scope_config=scope.scope_config,
        schema_catalog=dict(catalog.resources),
        schema_catalog_revision=scope.schema_catalog_revision + 1,
        read_policy_revision=scope.read_policy_revision,
        execution_budget_policy=scope.execution_budget_policy,
        normalization_policy_revision=scope.normalization_policy_revision,
        revision=scope.revision + 1,
    )
    session.add(new_scope)
    row.last_introspected_at = now
    await session.commit()
    return {
        "provider": catalog.provider,
        "version": catalog.version,
        "resources": catalog.resources,
        "scope_revision": new_scope.revision,
    }


@router.delete("/workspaces/{workspace_id}/evidence-connectors/{connector_id}", status_code=204)
async def disable_connector(
    workspace_id: int,
    connector_id: int,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(EvidenceConnector, connector_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "connector_not_found", "Evidence connector not found.")
    row.state = "disabled"
    row.instance_revision += 1
    session.add(
        _audit(user, "evidence_connector.disable", "evidence_connector", row.id, workspace_id)
    )
    await session.commit()
    return Response(status_code=204)
