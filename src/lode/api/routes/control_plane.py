"""Final provider, Workspace, repository, and connector control plane."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from aiokafka.admin import AIOKafkaAdminClient
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.control_schemas import (
    ConnectorCreate,
    ConnectorOut,
    GitAccountCreate,
    GitAccountOut,
    GitAccountPatch,
    GitBranchOut,
    GitBranchPageOut,
    GitAccountRepositoryOut,
    GitAccountTokenRotate,
    IngestionStart,
    ModelBindingInput,
    ModelBindingOut,
    ModelBindingPatch,
    ProviderAccountModelOut,
    ProviderAccountModelSelection,
    ProviderAccountConnectionInput,
    ProviderModelCatalogOut,
    ProviderModelDiscoveryOut,
    ProviderModelSelectionItem,
    ModelPolicyInput,
    ModelPolicyOut,
    PlatformSettingsOut,
    PlatformSettingsUpdate,
    ProviderAccountCreate,
    ProviderAccountOut,
    ProviderAccountPatch,
    RepositoryBind,
    RepositoryAnalysisJobOut,
    RepositoryAnalysisIssueOut,
    RepositoryAnalysisIssuePageOut,
    RepositoryBindingOut,
    RepositoryBindingPatch,
    WorkspaceCreate,
    WorkspacePatch,
    WorkspaceArchitectureContextOut,
    WorkspaceArchitectureContextPut,
    WorkspaceOut,
    WorkspaceReadinessOut,
)
from lode.api.schemas import WorkspaceMemberOut, WorkspaceMemberPutIn
from lode.api.types import EntityId
from lode.api.deps import assert_workspace_permission, require_admin, require_user
from lode.api.audit import audit_action
from lode.ai_output import SUPPORTED_AI_OUTPUT_LANGUAGES
from lode.application.intake import canonical_hash
from lode.config import kafka_security_kwargs, settings
from lode.crypto import CryptoError, decrypt_secret, encrypt_secret
from lode.db.models import (
    AIProviderAccount,
    AuditEvent,
    ContextPolicyRevision,
    EvidenceAccessScope,
    EvidenceConnector,
    GitAccount,
    GitAccountCredentialRevision,
    GitAccountRepositoryAccess,
    GitAccountSyncJob,
    GitRepository,
    Investigation,
    ProviderAccountModel,
    ModelPolicyRevision,
    ModelRoutingDecision,
    PlatformSettings,
    User,
    Workspace,
    WorkspaceModelBinding,
    WorkspacePermission,
    WorkspaceRepositoryBinding,
    WorkspaceIngestionRuntime,
    WorkspaceArchitectureContextRevision,
    RepositoryAnalysisJob,
    RepositoryAnalysisIssue,
)
from lode.db.session import AsyncSessionLocal
from lode.engine.llm import ModelConfig, ResponseSchema, complete_with_usage
from lode.evidence_connectors.registry import (
    create_evidence_connector,
    native_connector_capabilities,
)
from lode.evidence_access.loki_scope import normalize_loki_filter
from lode.evidence_connectors.common import response_json
from lode.evidence_connectors.types import IntrospectionBudget, ProviderExecutionError
from lode.git_accounts import (
    GitAccountSecret,
    credential_identity_hash as git_credential_identity_hash,
    encode_credential_secret,
)
from lode.git_accounts.providers import (
    GitProviderError,
    authenticate_access_token,
    endpoint_identity_hash,
    list_branches as list_provider_branches,
    list_repositories as list_provider_repositories,
    registered_adapters,
    require_adapter,
    resolve_api_url,
    verify_branch as verify_provider_branch,
)
from lode.infrastructure.provider_http import provider_endpoint, provider_request, validate_provider_endpoint
from lode.model_catalog import CATALOG_REVISION, find_model, require_model, supported_models
from lode.runtime_defaults import LLM_PROBE_TIMEOUT_SECONDS, KAFKA_TOPIC_VALIDATION_TIMEOUT_SECONDS

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


async def _active_user(session: AsyncSession, user_id: EntityId) -> User:
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise _error(401, "active_user_required", "An active user is required.")
    return user


async def _workspace_access(
    session: AsyncSession, user_id: EntityId, workspace_id: EntityId, permission: str
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


def _account_model_out(row: ProviderAccountModel) -> ProviderAccountModelOut:
    raise RuntimeError("account model output requires the owning provider account")


def _account_model_out_for_account(
    row: ProviderAccountModel, account: AIProviderAccount
) -> ProviderAccountModelOut:
    profile = require_model(account.provider_kind, account.protocol_id, row.provider_model_id)
    return ProviderAccountModelOut(
        id=row.id,
        provider_account_id=row.provider_account_id,
        provider_model_id=row.provider_model_id,
        display_name=profile.display_name,
        capabilities=dict(profile.capabilities),
        discovery_state=row.discovery_state,
        availability_state=row.availability_state,
        health_checked_at=row.health_checked_at,
        state=row.state,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _provider_out(session: AsyncSession, row: AIProviderAccount) -> ProviderAccountOut:
    models = tuple(
        (
            await session.execute(
                select(ProviderAccountModel)
                .where(ProviderAccountModel.provider_account_id == row.id)
                .order_by(ProviderAccountModel.provider_model_id)
            )
        )
        .scalars()
        .all()
    )
    return ProviderAccountOut(
        id=row.id,
        name=row.name,
        provider_kind=row.provider_kind,
        protocol_id=row.protocol_id,
        base_url=row.base_url,
        state=row.state,
        verification_status=row.verification_status,
        verified_at=row.verified_at,
        models=[_account_model_out_for_account(model, row) for model in models],
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _binding_out(row: WorkspaceModelBinding) -> ModelBindingOut:
    return ModelBindingOut.model_validate(row)


def _platform_settings_out(row: PlatformSettings) -> PlatformSettingsOut:
    return PlatformSettingsOut(
        ai_output_language=row.ai_output_language,
        revision=row.revision,
        updated_at=row.updated_at,
        supported_languages=list(SUPPORTED_AI_OUTPUT_LANGUAGES),
    )


def _audit(user: User, action: str, target_type: str, target_id: EntityId, workspace_id=None):
    return AuditEvent(
        actor_id=user.id,
        actor_username=user.username,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        workspace_id=workspace_id,
        result="ok",
        detail={},
    )


@router.get("/platform-settings", response_model=PlatformSettingsOut)
async def get_platform_settings(
    _: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    row = await session.get(PlatformSettings, 1)
    if row is None:
        raise _error(503, "platform_settings_unavailable", "Platform settings are unavailable.")
    return _platform_settings_out(row)


@router.put("/platform-settings", response_model=PlatformSettingsOut)
async def put_platform_settings(
    payload: PlatformSettingsUpdate,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = await session.scalar(
        select(PlatformSettings).where(PlatformSettings.id == 1).with_for_update()
    )
    if row is None:
        raise _error(503, "platform_settings_unavailable", "Platform settings are unavailable.")
    if payload.expected_revision != row.revision:
        raise _error(
            409,
            "platform_settings_revision_conflict",
            "Platform settings changed. Reload and try again.",
            current_revision=row.revision,
        )
    row.ai_output_language = payload.ai_output_language
    row.revision += 1
    row.updated_by = user.id
    session.add(_audit(user, "platform_settings.update", "platform_settings", row.id))
    await session.commit()
    await session.refresh(row)
    return _platform_settings_out(row)


@router.get("/ai-provider-accounts", response_model=list[ProviderAccountOut])
async def list_provider_accounts(
    _: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(select(AIProviderAccount).order_by(AIProviderAccount.id))
    ).scalars()
    return [await _provider_out(session, row) for row in rows]


def _validate_provider_protocol(provider_kind: str, protocol_id: str) -> None:
    if provider_kind == "openai" and protocol_id in {
        "openai.responses.v1",
        "openai.chat_completions.v1",
    }:
        return
    if provider_kind == "anthropic" and protocol_id == "anthropic.messages.v1":
        return
    raise _error(
        422,
        "provider_protocol_mismatch",
        "The selected protocol is not available for this provider.",
    )


async def _discover_provider_models(
    *, provider_kind: str, base_url: str, api_key: str
) -> tuple[str, ...]:
    if provider_kind == "openai":
        endpoint = provider_endpoint(base_url, "/models")
        headers = {"authorization": f"Bearer {api_key}", "accept": "application/json"}
    else:
        endpoint = provider_endpoint(base_url, "/v1/models")
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "accept": "application/json",
        }
    discovered: set[str] = set()
    after_id: str | None = None
    for _ in range(10):
        query = (
            {"limit": "100", **({"after_id": after_id} if after_id else {})}
            if provider_kind == "anthropic"
            else None
        )
        response = await provider_request(
            "GET",
            endpoint,
            headers=headers,
            timeout_seconds=LLM_PROBE_TIMEOUT_SECONDS,
            query=query,
        )
        payload = response_json(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderExecutionError("invalid_response", "provider model list is invalid")
        page_ids: list[str] = []
        for item in payload["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ProviderExecutionError("invalid_response", "provider model item is invalid")
            model_id = item["id"]
            if not model_id or len(model_id) > 200 or model_id != model_id.strip():
                raise ProviderExecutionError("invalid_response", "provider model ID is invalid")
            page_ids.append(model_id)
            discovered.add(model_id)
            if len(discovered) > 1_000:
                raise ProviderExecutionError("cost_exceeded", "provider model list is too large")
        if provider_kind == "openai" or payload.get("has_more") is not True:
            break
        last_id = payload.get("last_id")
        if not isinstance(last_id, str) or not last_id or last_id == after_id or not page_ids:
            raise ProviderExecutionError("invalid_response", "provider model pagination is invalid")
        after_id = last_id
    else:
        raise ProviderExecutionError("cost_exceeded", "provider model pagination is too large")
    return tuple(sorted(discovered))


def _discovery_out(
    provider_kind: str, protocol_id: str, discovered: tuple[str, ...]
) -> ProviderModelDiscoveryOut:
    supported = {profile.model_id for profile in supported_models(provider_kind, protocol_id)}
    return ProviderModelDiscoveryOut(
        catalog_revision=CATALOG_REVISION,
        available_model_ids=tuple(model_id for model_id in discovered if model_id in supported),
        unsupported_model_ids=tuple(model_id for model_id in discovered if model_id not in supported),
    )


async def _probe_model(
    account: AIProviderAccount,
    model_id: str,
    *,
    api_key_ciphertext: str | None = None,
) -> tuple[bool, str | None]:
    result = await complete_with_usage(
        "You are a protocol health probe.",
        "Return a JSON object with the field ok set to true.",
        ModelConfig(
            protocol_id=account.protocol_id,
            base_url=account.base_url,
            api_key_ciphertext=api_key_ciphertext or account.api_key_ciphertext,
            model=model_id,
            max_completion_tokens=32,
        ),
        json_mode=True,
        response_schema=ResponseSchema(
            name="protocol_health", schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        ),
        timeout_seconds=LLM_PROBE_TIMEOUT_SECONDS,
    )
    return bool(result.text), result.error_code


async def _apply_model_selection(
    session: AsyncSession,
    account: AIProviderAccount,
    *,
    models: tuple[ProviderModelSelectionItem, ...],
    discovered_ids: frozenset[str],
    reset_health: bool,
    api_key_ciphertext: str | None = None,
) -> None:
    profiles = {}
    selections = {item.provider_model_id: item for item in models}
    for item in models:
        profile = find_model(account.provider_kind, account.protocol_id, item.provider_model_id)
        if profile is None:
            raise _error(
                422,
                "unsupported_model",
                "The model is not in the reviewed provider catalog.",
                model_id=item.provider_model_id,
            )
        profiles[item.provider_model_id] = profile
    rows = tuple(
        (
            await session.execute(
                select(ProviderAccountModel).where(
                    ProviderAccountModel.provider_account_id == account.id
                )
            )
        )
        .scalars()
        .all()
    )
    existing = {row.provider_model_id: row for row in rows}
    for model_id, profile in profiles.items():
        selection = selections[model_id]
        missing = selection.source == "discovered" and model_id not in discovered_ids
        row = existing.get(model_id)
        if missing:
            if row is None:
                session.add(
                    ProviderAccountModel(
                        provider_account_id=account.id,
                        provider_model_id=model_id,
                        catalog_revision=profile.catalog_revision,
                        catalog_profile_hash=profile.profile_hash,
                        discovery_state="missing",
                        availability_state="unavailable",
                        state="disabled",
                    )
                )
            else:
                row.discovery_state = "missing"
                row.availability_state = "unavailable"
                row.state = "disabled"
                row.revision += 1
            continue
        available, error_code = await _probe_model(
            account, model_id, api_key_ciphertext=api_key_ciphertext
        )
        if not available:
            raise _error(
                422,
                "model_probe_failed",
                "The selected model did not pass its structured output probe.",
                model_id=model_id,
                provider_error=error_code,
            )
        if row is None:
            session.add(
                ProviderAccountModel(
                    provider_account_id=account.id,
                    provider_model_id=model_id,
                    catalog_revision=profile.catalog_revision,
                    catalog_profile_hash=profile.profile_hash,
                    discovery_state=selection.source,
                    availability_state="healthy",
                    health_checked_at=datetime.now(UTC),
                    state="active",
                )
            )
            continue
        changed = (
            row.catalog_revision != profile.catalog_revision
            or row.catalog_profile_hash != profile.profile_hash
            or row.state != "active"
        )
        row.catalog_revision = profile.catalog_revision
        row.catalog_profile_hash = profile.profile_hash
        row.discovery_state = selection.source
        row.availability_state = "healthy"
        row.health_checked_at = datetime.now(UTC)
        row.state = "active"
        if changed or reset_health:
            row.revision += 1
    for row in rows:
        if row.provider_model_id in profiles:
            continue
        active_binding = await session.scalar(
            select(WorkspaceModelBinding.id).where(
                WorkspaceModelBinding.provider_account_model_id == row.id,
                WorkspaceModelBinding.state == "active",
            )
        )
        if active_binding is not None:
            raise _error(
                409,
                "account_model_in_use",
                "Disable the active Workspace binding before removing this account model.",
            )
        if row.state != "disabled":
            row.state = "disabled"
            row.revision += 1


async def _sync_provider_verification_status(
    session: AsyncSession, account: AIProviderAccount
) -> None:
    await session.flush()
    active_model_id = await session.scalar(
        select(ProviderAccountModel.id).where(
            ProviderAccountModel.provider_account_id == account.id,
            ProviderAccountModel.state == "active",
            ProviderAccountModel.availability_state == "healthy",
        )
    )
    account.verification_status = "healthy" if active_model_id is not None else "unavailable"
    account.verified_at = datetime.now(UTC)


async def _safe_discover(
    *, provider_kind: str, protocol_id: str, base_url: str, api_key: str
) -> tuple[str, ...]:
    _validate_provider_protocol(provider_kind, protocol_id)
    try:
        return await _discover_provider_models(
            provider_kind=provider_kind,
            base_url=_validate_provider_url(base_url),
            api_key=api_key,
        )
    except ProviderExecutionError as exc:
        raise _error(
            422,
            f"model_discovery_{exc.code}",
            "The provider model list could not be loaded.",
        ) from exc


@router.get("/ai-provider-model-catalog", response_model=list[ProviderModelCatalogOut])
async def get_provider_model_catalog(
    provider_kind: str = Query(),
    protocol_id: str = Query(),
    _: int = Depends(require_admin),
) -> list[ProviderModelCatalogOut]:
    _validate_provider_protocol(provider_kind, protocol_id)
    return [
        ProviderModelCatalogOut(
            provider_kind=profile.provider_kind,
            provider_model_id=profile.model_id,
            display_name=profile.display_name,
            context_window_tokens=profile.context_window_tokens,
            max_output_tokens=profile.max_output_tokens,
            capabilities=dict(profile.capabilities),
            protocol_ids=profile.protocol_ids,
            catalog_revision=profile.catalog_revision,
            source_url=profile.source_url,
            reviewed_at=profile.reviewed_at,
        )
        for profile in supported_models(provider_kind, protocol_id)
    ]


@router.post(
    "/ai-provider-accounts/discover-models", response_model=ProviderModelDiscoveryOut
)
async def discover_unsaved_provider_models(
    payload: ProviderAccountConnectionInput,
    _: int = Depends(require_admin),
) -> ProviderModelDiscoveryOut:
    discovered = await _safe_discover(
        provider_kind=payload.provider_kind,
        protocol_id=payload.protocol_id,
        base_url=payload.base_url,
        api_key=payload.api_key,
    )
    return _discovery_out(payload.provider_kind, payload.protocol_id, discovered)


@router.post(
    "/ai-provider-accounts/{account_id}/discover-models",
    response_model=ProviderModelDiscoveryOut,
)
async def discover_saved_provider_models(
    account_id: EntityId,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ProviderModelDiscoveryOut:
    user = await _active_user(session, user_id)
    row = await session.get(AIProviderAccount, account_id)
    if row is None:
        raise _error(404, "provider_account_not_found", "Provider account not found.")
    api_key = decrypt_secret(row.api_key_ciphertext)
    if not api_key:
        raise _error(422, "provider_api_key_unavailable", "Provider API Key is unavailable.")
    discovered = await _safe_discover(
        provider_kind=row.provider_kind,
        protocol_id=row.protocol_id,
        base_url=row.base_url,
        api_key=api_key,
    )
    current = tuple(
        (
            await session.execute(
                select(ProviderAccountModel).where(
                    ProviderAccountModel.provider_account_id == row.id,
                )
            )
        ).scalars()
    )
    await _apply_model_selection(
        session,
        row,
        models=tuple(
            ProviderModelSelectionItem(
                provider_model_id=model.provider_model_id,
                source=model.discovery_state if model.discovery_state != "missing" else "discovered",
            )
            for model in current
        ),
        discovered_ids=frozenset(discovered),
        reset_health=False,
    )
    await _sync_provider_verification_status(session, row)
    session.add(_audit(user, "provider_account.models.discover", "ai_provider_account", row.id))
    await session.commit()
    return _discovery_out(row.provider_kind, row.protocol_id, discovered)


@router.post("/ai-provider-accounts", response_model=ProviderAccountOut, status_code=201)
async def create_provider_account(
    payload: ProviderAccountCreate,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    base_url = _validate_provider_url(payload.base_url)
    row = AIProviderAccount(
        name=payload.name.strip(),
        provider_kind=payload.provider_kind,
        protocol_id=payload.protocol_id,
        base_url=base_url,
        api_key_ciphertext=encrypt_secret(payload.api_key) or "",
        verification_status="untested",
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(
            409, "provider_name_conflict", "Provider account name is already used."
        ) from exc
    discovered = (
        await _safe_discover(
            provider_kind=payload.provider_kind,
            protocol_id=payload.protocol_id,
            base_url=base_url,
            api_key=payload.api_key,
        )
        if any(item.source == "discovered" for item in payload.models)
        else ()
    )
    await _apply_model_selection(
        session,
        row,
        models=payload.models,
        discovered_ids=frozenset(discovered),
        reset_health=True,
    )
    await _sync_provider_verification_status(session, row)
    session.add(_audit(user, "provider_account.create", "ai_provider_account", row.id))
    await session.commit()
    await session.refresh(row)
    return await _provider_out(session, row)


@router.patch("/ai-provider-accounts/{account_id}", response_model=ProviderAccountOut)
async def patch_provider_account(
    account_id: EntityId,
    payload: ProviderAccountPatch,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = await session.get(AIProviderAccount, account_id)
    if row is None:
        raise _error(404, "provider_account_not_found", "Provider account not found.")
    values = payload.model_dump(exclude_unset=True)
    api_key = values.pop("api_key", None)
    models = values.pop("models", None)
    if "base_url" in values:
        values["base_url"] = _validate_provider_url(values["base_url"])
    connection_changed = (
        "base_url" in values
        or "protocol_id" in values
        or "provider_kind" in values
        or api_key is not None
    )
    if connection_changed and models is None:
        raise _error(
            422,
            "model_selection_required",
            "Changing a provider connection requires a refreshed model selection.",
        )
    effective_api_key = api_key or decrypt_secret(row.api_key_ciphertext)
    if models is not None and not effective_api_key:
        raise _error(422, "provider_api_key_unavailable", "Provider API Key is unavailable.")
    effective_provider_kind = values.get("provider_kind", row.provider_kind)
    effective_protocol_id = values.get("protocol_id", row.protocol_id)
    _validate_provider_protocol(effective_provider_kind, effective_protocol_id)
    for key, value in values.items():
        setattr(row, key, value)
    if api_key is not None:
        row.api_key_ciphertext = encrypt_secret(api_key) or ""
    row.revision += 1
    if models is not None:
        discovered = (
            await _safe_discover(
                provider_kind=row.provider_kind,
                protocol_id=row.protocol_id,
                base_url=row.base_url,
                api_key=effective_api_key,
            )
            if any(item.source == "discovered" for item in models)
            else ()
        )
        probe_api_key = encrypt_secret(effective_api_key) or ""
        await _apply_model_selection(
            session,
            row,
            models=models,
            discovered_ids=frozenset(discovered),
            reset_health=connection_changed,
            api_key_ciphertext=probe_api_key,
        )
        await _sync_provider_verification_status(session, row)
    session.add(_audit(user, "provider_account.update", "ai_provider_account", row.id))
    await session.commit()
    await session.refresh(row)
    return await _provider_out(session, row)


@router.put("/ai-provider-accounts/{account_id}/models", response_model=ProviderAccountOut)
async def update_provider_account_models(
    account_id: EntityId,
    payload: ProviderAccountModelSelection,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = await session.get(AIProviderAccount, account_id)
    if row is None:
        raise _error(404, "provider_account_not_found", "Provider account not found.")
    api_key = decrypt_secret(row.api_key_ciphertext)
    if not api_key:
        raise _error(422, "provider_api_key_unavailable", "Provider API Key is unavailable.")
    discovered = (
        await _safe_discover(
            provider_kind=row.provider_kind,
            protocol_id=row.protocol_id,
            base_url=row.base_url,
            api_key=api_key,
        )
        if any(item.source == "discovered" for item in payload.models)
        else ()
    )
    await _apply_model_selection(
        session,
        row,
        models=payload.models,
        discovered_ids=frozenset(discovered),
        reset_health=False,
    )
    await _sync_provider_verification_status(session, row)
    row.revision += 1
    session.add(_audit(user, "provider_account.models.update", "ai_provider_account", row.id))
    await session.commit()
    await session.refresh(row)
    return await _provider_out(session, row)


@router.delete("/ai-provider-accounts/{account_id}", status_code=204)
async def disable_provider_account(
    account_id: EntityId,
    user_id: EntityId = Depends(require_admin),
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


@router.post("/ai-provider-accounts/{account_id}/models/{account_model_id}/test")
async def test_provider_account_model(
    account_id: EntityId,
    account_model_id: EntityId,
    _: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(ProviderAccountModel, account_model_id)
    provider = await session.get(AIProviderAccount, account_id)
    if row is None or provider is None or row.provider_account_id != provider.id:
        raise _error(404, "provider_account_model_not_found", "Account model not found.")
    if row.state != "active" or provider.state != "active":
        raise _error(422, "account_model_ineligible", "An active account model is required.")
    profile = require_model(
        provider.provider_kind,
        provider.protocol_id,
        row.provider_model_id,
    )
    if (
        row.catalog_revision != profile.catalog_revision
        or row.catalog_profile_hash != profile.profile_hash
    ):
        raise _error(409, "model_catalog_changed", "Resync the account model after catalog changes.")
    result = await complete_with_usage(
        "You are a protocol health probe.",
        "Reply with OK.",
        ModelConfig(
            protocol_id=provider.protocol_id,
            base_url=provider.base_url,
            api_key_ciphertext=provider.api_key_ciphertext,
            model=row.provider_model_id,
            max_completion_tokens=16,
        ),
        timeout_seconds=LLM_PROBE_TIMEOUT_SECONDS,
    )
    row.availability_state = "healthy" if result.text else "unavailable"
    row.revision += 1
    row.health_checked_at = datetime.now(UTC)
    await session.commit()
    return {
        "available": bool(result.text),
        "latency_ms": result.latency_ms,
        "error_code": result.error_code,
    }


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    row = Workspace(
        name=payload.name,
        description=payload.description,
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
    architecture_context = WorkspaceArchitectureContextRevision(
        workspace_id=row.id,
        entries=[],
        revision=1,
        created_by=user.id,
    )
    session.add(architecture_context)
    await session.flush()
    row.architecture_context_revision_id = architecture_context.id
    session.add(_audit(user, "workspace.create", "workspace", row.id, row.id))
    await session.commit()
    await session.refresh(row)
    return WorkspaceOut.model_validate(row)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def patch_workspace(
    workspace_id: EntityId,
    payload: WorkspacePatch,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    changes = payload.model_dump(exclude_unset=True)
    next_topic = changes.pop("ingestion_topic", None)
    if next_topic is not None and next_topic != workspace.ingestion_topic:
        if workspace.ingestion_state == "active":
            raise _error(
                409,
                "ingestion_topic_change_requires_pause",
                "Pause ingestion before changing its Kafka topic.",
            )
        workspace.ingestion_topic = next_topic
        workspace.ingestion_state = "draft"
        workspace.ingestion_start_position = None
        workspace.ingestion_activation_kind = None
        workspace.ingestion_started_at = None
        workspace.ingestion_paused_at = None
    for key, value in changes.items():
        setattr(workspace, key, value)
    session.add(_audit(user, "workspace.update", "workspace", workspace.id, workspace.id))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(
            409, "workspace_topic_conflict", "Ingestion topic is already assigned."
        ) from exc
    await session.refresh(workspace)
    return WorkspaceOut.model_validate(workspace)


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(
    user_id: EntityId = Depends(require_user), session: AsyncSession = Depends(get_session)
):
    user = await _active_user(session, user_id)
    statement = select(Workspace).order_by(Workspace.name, Workspace.id)
    rows = (await session.execute(statement)).scalars().unique()
    return [WorkspaceOut.model_validate(row) for row in rows]


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, row = await _workspace_access(session, user_id, workspace_id, "read")
    return WorkspaceOut.model_validate(row)


@router.get("/workspaces/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
async def list_workspace_members(
    workspace_id: EntityId,
    _: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceMemberOut]:
    if await session.get(Workspace, workspace_id) is None:
        raise _error(404, "workspace_not_found", "Workspace not found.")
    rows = (
        await session.execute(
            select(WorkspacePermission, User)
            .join(User, User.id == WorkspacePermission.user_id)
            .where(WorkspacePermission.workspace_id == workspace_id)
            .order_by(User.username)
        )
    ).all()
    return [
        WorkspaceMemberOut(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            permission=grant.permission,
        )
        for grant, user in rows
    ]


@router.put("/workspaces/{workspace_id}/members/{member_id}", response_model=WorkspaceMemberOut)
async def put_workspace_member(
    workspace_id: EntityId,
    member_id: EntityId,
    payload: WorkspaceMemberPutIn,
    admin_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceMemberOut:
    if await session.get(Workspace, workspace_id) is None:
        raise _error(404, "workspace_not_found", "Workspace not found.")
    user = await session.get(User, member_id)
    if user is None or user.is_system_admin:
        raise _error(404, "workbench_user_not_found", "Regular user not found.")
    grant = await session.get(WorkspacePermission, (member_id, workspace_id))
    if grant is None:
        grant = WorkspacePermission(
            user_id=member_id, workspace_id=workspace_id, permission=payload.permission
        )
        session.add(grant)
        action = "workspace.member.grant"
    else:
        grant.permission = payload.permission
        action = "workspace.member.update"
    await session.commit()
    await audit_action(
        action=action,
        actor_id=admin_id,
        target_type="workspace_permission",
        target_id=f"{member_id}:{workspace_id}",
        workspace_id=workspace_id,
    )
    return WorkspaceMemberOut(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        permission=grant.permission,
    )


@router.delete("/workspaces/{workspace_id}/members/{member_id}", status_code=204)
async def delete_workspace_member(
    workspace_id: EntityId,
    member_id: EntityId,
    admin_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    grant = await session.get(WorkspacePermission, (member_id, workspace_id))
    if grant is None:
        raise _error(404, "workspace_member_not_found", "Workspace member not found.")
    await session.delete(grant)
    await session.commit()
    await audit_action(
        action="workspace.member.revoke",
        actor_id=admin_id,
        target_type="workspace_permission",
        target_id=f"{member_id}:{workspace_id}",
        workspace_id=workspace_id,
    )


@router.post("/admin/investigations/{investigation_id}/archive")
async def archive_investigation_as_admin(
    investigation_id: EntityId,
    admin_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    row = await session.get(Investigation, investigation_id)
    if row is None:
        raise _error(404, "investigation_not_found", "Investigation not found.")
    if row.status not in {"completed", "failed", "cancelled"}:
        raise _error(409, "investigation_not_terminal", "Only a terminal investigation can archive.")
    if row.archived_at is not None:
        raise _error(409, "investigation_already_archived", "Investigation is already archived.")
    row.archived_at = datetime.now(UTC)
    row.archived_by = admin_id
    await session.commit()
    await audit_action(
        action="investigation.archive",
        actor_id=admin_id,
        target_type="investigation",
        target_id=str(investigation_id),
        workspace_id=row.workspace_id,
    )
    return {"status": "ok"}


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


async def _workspace_readiness(session: AsyncSession, workspace: Workspace) -> WorkspaceReadinessOut:
    checks: list[dict] = []
    topic_details: dict = {"topic": workspace.ingestion_topic}
    topic_ready = bool(workspace.ingestion_topic.strip())
    reachable = False
    if topic_ready:
        try:
            reachable = await asyncio.wait_for(
                _broker_has_topic(workspace.ingestion_topic),
                timeout=KAFKA_TOPIC_VALIDATION_TIMEOUT_SECONDS,
            )
        except Exception:
            reachable = False
    topic_details["reachable"] = reachable
    checks.append(
        {
            "code": "kafka_topic",
            "outcome": "passed" if topic_ready and reachable else "blocked",
            "details": topic_details,
        }
    )

    policy = (
        await session.get(ModelPolicyRevision, workspace.model_policy_revision_id)
        if workspace.model_policy_revision_id is not None
        else None
    )
    model_details: dict = {"missing_roles": sorted(_REQUIRED_MODEL_ROLES)}
    model_ready = False
    if policy is None:
        model_details["reason"] = "not_published"
    else:
        expected_revisions = {
            int(item["binding_id"]): int(item["revision"]) for item in policy.eligible_bindings
        }
        binding_ids = list(expected_revisions)
        bindings = tuple(
            (
                await session.execute(
                    select(WorkspaceModelBinding, ProviderAccountModel, AIProviderAccount)
                    .join(
                        ProviderAccountModel,
                        ProviderAccountModel.id == WorkspaceModelBinding.provider_account_model_id,
                    )
                    .join(
                        AIProviderAccount,
                        AIProviderAccount.id == ProviderAccountModel.provider_account_id,
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
        missing_roles = sorted(_REQUIRED_MODEL_ROLES - roles)
        model_details = {
            "policy_revision": policy.revision,
            "eligible_binding_count": len(bindings),
            "missing_roles": missing_roles,
        }
        model_ready = not missing_roles
    checks.append(
        {
            "code": "model_policy",
            "outcome": "passed" if model_ready else "blocked",
            "details": model_details,
        }
    )

    active_repositories = tuple(
        (
            await session.execute(
                select(WorkspaceRepositoryBinding)
                .where(
                    WorkspaceRepositoryBinding.workspace_id == workspace.id,
                    WorkspaceRepositoryBinding.state == "active",
                )
                .order_by(WorkspaceRepositoryBinding.id)
            )
        ).scalars()
    )
    repository_count = len(active_repositories)
    latest_analysis = (
        await session.execute(
            select(RepositoryAnalysisJob)
            .where(
                RepositoryAnalysisJob.workspace_id == workspace.id,
                RepositoryAnalysisJob.state == "succeeded",
            )
            .order_by(RepositoryAnalysisJob.finished_at.desc(), RepositoryAnalysisJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    active_binding_ids = [row.id for row in active_repositories]
    analysis_current = bool(
        latest_analysis is not None
        and sorted(latest_analysis.requested_binding_ids) == active_binding_ids
        and latest_analysis.finished_at is not None
        and all(row.updated_at <= latest_analysis.finished_at for row in active_repositories)
    )
    checks.append(
        {
            "code": "repositories",
            "outcome": "passed" if analysis_current else "warning",
            "details": {
                "active_count": repository_count,
                "analysis_current": analysis_current,
                "analysis_job_id": None if latest_analysis is None else latest_analysis.id,
            },
        }
    )
    connector_count = int(
        await session.scalar(
            select(func.count())
            .select_from(EvidenceConnector)
            .where(
                EvidenceConnector.workspace_id == workspace.id,
                EvidenceConnector.state == "active",
                EvidenceConnector.verification_status == "healthy",
            )
        )
        or 0
    )
    checks.append(
        {
            "code": "evidence_connectors",
            "outcome": "passed" if connector_count else "warning",
            "details": {"healthy_count": connector_count},
        }
    )
    architecture_context = (
        await session.get(
            WorkspaceArchitectureContextRevision,
            workspace.architecture_context_revision_id,
        )
        if workspace.architecture_context_revision_id is not None
        else None
    )
    context_count = (
        len(architecture_context.entries)
        if architecture_context is not None and architecture_context.workspace_id == workspace.id
        else 0
    )
    checks.append(
        {
            "code": "architecture_context",
            "outcome": "passed" if context_count else "warning",
            "details": {
                "entry_count": context_count,
                "revision": None if architecture_context is None else architecture_context.revision,
            },
        }
    )
    runtime = await session.get(WorkspaceIngestionRuntime, workspace.id)
    runtime_payload = {
        "observed_state": "idle" if runtime is None else runtime.observed_state,
        "observed_version": 0 if runtime is None else runtime.observed_version,
        "consumer_id": None if runtime is None else runtime.consumer_id,
        "assigned_partitions": 0 if runtime is None else runtime.assigned_partitions,
        "backlog": None if runtime is None else runtime.backlog,
        "last_heartbeat_at": None if runtime is None else runtime.last_heartbeat_at,
        "last_error": None if runtime is None else runtime.last_error,
    }
    return WorkspaceReadinessOut(
        workspace_id=workspace.id,
        can_start=all(item["outcome"] != "blocked" for item in checks),
        checks=tuple(checks),
        runtime=runtime_payload,
    )


async def _set_ingestion(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    target: str,
    start_position: str | None = None,
    activation_kind: str | None = None,
):
    if target == "active":
        readiness = await _workspace_readiness(session, workspace)
        if not readiness.can_start:
            raise _error(
                409,
                "workspace_not_ready",
                "Complete all required Workspace settings before starting ingestion.",
                blockers=[
                    {"code": item.code, "details": item.details}
                    for item in readiness.checks
                    if item.outcome == "blocked"
                ],
            )
        workspace.ingestion_version += 1
        workspace.ingestion_activation_kind = activation_kind
        if activation_kind == "start":
            workspace.ingestion_start_position = start_position
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
    workspace_id: EntityId,
    payload: IngestionStart,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    if workspace.ingestion_state != "draft":
        raise _error(409, "ingestion_transition_invalid", "Only draft ingestion can start.")
    return await _set_ingestion(
        session, user, workspace, "active", payload.start_position, "start"
    )


@router.post("/workspaces/{workspace_id}/ingestion/pause", response_model=WorkspaceOut)
async def pause_ingestion(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    if workspace.ingestion_state != "active":
        raise _error(409, "ingestion_transition_invalid", "Only active ingestion can pause.")
    return await _set_ingestion(session, user, workspace, "paused")


@router.post("/workspaces/{workspace_id}/ingestion/resume", response_model=WorkspaceOut)
async def resume_ingestion(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, workspace = await _workspace_access(session, user_id, workspace_id, "admin")
    if workspace.ingestion_state != "paused":
        raise _error(409, "ingestion_transition_invalid", "Only paused ingestion can resume.")
    return await _set_ingestion(session, user, workspace, "active", activation_kind="resume")


@router.get(
    "/workspaces/{workspace_id}/architecture-context",
    response_model=WorkspaceArchitectureContextOut,
)
async def get_workspace_architecture_context(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, workspace = await _workspace_access(session, user_id, workspace_id, "read")
    row = await session.get(
        WorkspaceArchitectureContextRevision,
        workspace.architecture_context_revision_id,
    )
    if row is None or row.workspace_id != workspace_id:
        raise _error(
            409,
            "architecture_context_missing",
            "Workspace architecture context is unavailable.",
        )
    return WorkspaceArchitectureContextOut.model_validate(row, from_attributes=True)


@router.put(
    "/workspaces/{workspace_id}/architecture-context",
    response_model=WorkspaceArchitectureContextOut,
)
async def put_workspace_architecture_context(
    workspace_id: EntityId,
    payload: WorkspaceArchitectureContextPut,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    assert workspace is not None
    revision = int(
        await session.scalar(
            select(func.coalesce(func.max(WorkspaceArchitectureContextRevision.revision), 0)).where(
                WorkspaceArchitectureContextRevision.workspace_id == workspace_id
            )
        )
        or 0
    ) + 1
    row = WorkspaceArchitectureContextRevision(
        workspace_id=workspace_id,
        entries=[item.model_dump() for item in payload.entries],
        revision=revision,
        created_by=user.id,
    )
    session.add(row)
    await session.flush()
    workspace.architecture_context_revision_id = row.id
    session.add(
        _audit(
            user,
            "workspace.architecture_context.publish",
            "workspace_architecture_context_revision",
            row.id,
            workspace_id,
        )
    )
    await session.commit()
    await session.refresh(row)
    return WorkspaceArchitectureContextOut.model_validate(row, from_attributes=True)


@router.get("/workspaces/{workspace_id}/model-bindings", response_model=list[ModelBindingOut])
async def list_model_bindings(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
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
    workspace_id: EntityId,
    payload: ModelBindingInput,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    deployment = await session.get(ProviderAccountModel, payload.provider_account_model_id)
    if deployment is None or deployment.state != "active":
        raise _error(422, "provider_account_model_ineligible", "Active account model required.")
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
    workspace_id: EntityId,
    binding_id: EntityId,
    payload: ModelBindingPatch,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(WorkspaceModelBinding, binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "model_binding_not_found", "Model binding not found.")
    values = payload.model_dump(exclude_unset=True, mode="json")
    state = values.pop("state", None)
    effective = ModelBindingInput(
        provider_account_model_id=row.provider_account_model_id,
        execution_classes=values.get("execution_classes", row.execution_classes),
        allowed_roles=values.get("allowed_roles", row.allowed_roles),
        priority=values.get("priority", row.priority),
        max_calls=values.get("max_calls", row.max_calls),
        max_cost_per_call=values.get("max_cost_per_call", row.max_cost_per_call),
        timeout_ms=values.get("timeout_ms", row.timeout_ms),
        allowed_data_classes=values.get("allowed_data_classes", row.allowed_data_classes),
        max_context_utilization=values.get("max_context_utilization", row.max_context_utilization),
    ).model_dump(mode="json")
    effective.pop("provider_account_model_id")
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
    workspace_id: EntityId,
    binding_id: EntityId,
    user_id: EntityId = Depends(require_user),
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
    workspace_id: EntityId,
    payload: ModelPolicyInput,
    user_id: EntityId = Depends(require_user),
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
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
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


@router.get(
    "/workspaces/{workspace_id}/readiness",
    response_model=WorkspaceReadinessOut,
)
async def workspace_readiness(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    _, workspace = await _workspace_access(session, user_id, workspace_id, "read")
    return await _workspace_readiness(session, workspace)


def _repository_out(
    binding: WorkspaceRepositoryBinding,
    repository: GitRepository,
    account: GitAccount,
):
    return RepositoryBindingOut(
        id=binding.id,
        workspace_id=binding.workspace_id,
        repository_id=repository.id,
        account_connection_id=account.id,
        account_name=account.name,
        external_account_login=account.external_account_login,
        provider_kind=repository.adapter_id,
        name=repository.name,
        full_name=repository.full_name,
        repo_url=repository.repo_url,
        web_url=repository.web_url,
        repo_type=repository.repo_type,
        default_branch=repository.default_branch,
        branch_mode=binding.branch_mode,
        branch_name=binding.branch_name,
        effective_branch=_effective_branch(binding, repository),
        role=binding.role,
        priority=binding.priority,
        description=binding.description,
        state=binding.state,
        revision=binding.revision,
    )


def _effective_branch(binding: WorkspaceRepositoryBinding, repository: GitRepository) -> str:
    return binding.branch_name if binding.branch_mode == "branch" else repository.default_branch


def _analysis_binding_snapshot(
    binding: WorkspaceRepositoryBinding,
    repository: GitRepository,
) -> dict[str, object]:
    return {
        "binding_id": binding.id,
        "configuration_revision": binding.descriptor_revision,
        "repository_id": repository.id,
        "account_connection_id": binding.account_connection_id,
        "role": binding.role,
        "branch_mode": binding.branch_mode,
        "effective_branch": _effective_branch(binding, repository),
    }


async def _analysis_input(
    session: AsyncSession,
    workspace_id: EntityId,
) -> tuple[list[dict[str, object]], str]:
    rows = (
        await session.execute(
            select(WorkspaceRepositoryBinding, GitRepository)
            .join(GitRepository, GitRepository.id == WorkspaceRepositoryBinding.repository_id)
            .where(
                WorkspaceRepositoryBinding.workspace_id == workspace_id,
                WorkspaceRepositoryBinding.state == "active",
            )
            .order_by(WorkspaceRepositoryBinding.id)
        )
    ).all()
    snapshot = [_analysis_binding_snapshot(binding, repository) for binding, repository in rows]
    return snapshot, canonical_hash({"repository_bindings": snapshot})


async def _repository_analysis_out(
    session: AsyncSession,
    row: RepositoryAnalysisJob,
) -> RepositoryAnalysisJobOut:
    _, current_hash = await _analysis_input(session, row.workspace_id)
    return RepositoryAnalysisJobOut.model_validate(row).model_copy(
        update={"is_current": row.state == "succeeded" and row.input_hash == current_hash}
    )


async def _git_account_out(
    session: AsyncSession, row: GitAccount
) -> GitAccountOut:
    repository_count = int(
        await session.scalar(
            select(func.count())
            .select_from(GitAccountRepositoryAccess)
            .where(
                GitAccountRepositoryAccess.account_connection_id == row.id,
                GitAccountRepositoryAccess.state == "available",
            )
        )
        or 0
    )
    return GitAccountOut(
        id=row.id,
        adapter_id=row.adapter_id,
        api_url=row.api_url,
        name=row.name,
        external_account_id=row.external_account_id,
        external_account_login=row.external_account_login,
        account_url=row.account_url,
        state=row.state,
        verification_status=row.verification_status,
        verified_at=row.verified_at,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        repository_count=repository_count,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
async def _append_account_credential(
    session: AsyncSession,
    account: GitAccount,
    *,
    username: str,
    token: str,
    expires_at: datetime | None = None,
) -> GitAccountCredentialRevision:
    latest_revision = int(
        await session.scalar(
            select(func.coalesce(func.max(GitAccountCredentialRevision.revision), 0)).where(
                GitAccountCredentialRevision.account_connection_id == account.id
            )
        )
        or 0
    )
    secret = GitAccountSecret(username=username, token=token)
    row = GitAccountCredentialRevision(
        account_connection_id=account.id,
        revision=latest_revision + 1,
        secret_ciphertext=encrypt_secret(encode_credential_secret(secret)) or "",
        credential_identity_hash=git_credential_identity_hash(secret),
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    account.current_credential_revision_id = row.id
    account.revision += 1
    return row


async def _account_secret(
    session: AsyncSession, account: GitAccount
) -> GitAccountSecret:
    if account.current_credential_revision_id is None:
        raise ValueError("Git account credential is unavailable")
    revision = await session.get(
        GitAccountCredentialRevision, account.current_credential_revision_id
    )
    if revision is None or revision.account_connection_id != account.id:
        raise ValueError("Git account credential is unavailable")
    try:
        from lode.git_accounts import decode_credential_secret

        secret = decode_credential_secret(decrypt_secret(revision.secret_ciphertext) or "")
    except (CryptoError, ValueError, TypeError) as exc:
        raise ValueError("Git account credential is unavailable") from exc
    if git_credential_identity_hash(secret) != revision.credential_identity_hash:
        raise ValueError("Git account credential identity is invalid")
    return secret


async def _sync_git_account(session: AsyncSession, account: GitAccount) -> GitAccountSyncJob:
    job = GitAccountSyncJob(account_connection_id=account.id, state="running", attempt=1)
    session.add(job)
    await session.flush()
    try:
        secret = await _account_secret(session, account)
        catalogue = await list_provider_repositories(
            adapter_id=account.adapter_id,
            api_url=account.api_url,
            token=secret.token,
        )
        visible_ids: set[int] = set()
        now = datetime.now(UTC)
        for item in catalogue:
            repository = (
                await session.execute(
                    select(GitRepository).where(
                        GitRepository.adapter_id == account.adapter_id,
                        GitRepository.endpoint_identity_hash == account.endpoint_identity_hash,
                        GitRepository.external_repository_id == item.external_id,
                    )
                )
            ).scalar_one_or_none()
            if repository is None:
                repository = GitRepository(
                    adapter_id=account.adapter_id,
                    endpoint_identity_hash=account.endpoint_identity_hash,
                    external_repository_id=item.external_id,
                    name=item.name,
                    full_name=item.full_name,
                    repo_url=item.clone_url,
                    web_url=item.web_url,
                    repo_type="git",
                    default_branch=item.default_branch,
                    visibility=item.visibility,
                    archived=item.archived,
                    pushed_at=item.pushed_at,
                )
                session.add(repository)
                await session.flush()
            else:
                repository.name = item.name
                repository.full_name = item.full_name
                repository.repo_url = item.clone_url
                repository.web_url = item.web_url
                repository.default_branch = item.default_branch
                repository.visibility = item.visibility
                repository.archived = item.archived
                repository.pushed_at = item.pushed_at
            visible_ids.add(repository.id)
            access = await session.get(GitAccountRepositoryAccess, (account.id, repository.id))
            if access is None:
                access = GitAccountRepositoryAccess(
                    account_connection_id=account.id,
                    repository_id=repository.id,
                    access_level="read",
                    state="available",
                    last_seen_at=now,
                )
                session.add(access)
            else:
                access.state = "available"
                access.last_seen_at = now
        prior_access = tuple(
            (
                await session.execute(
                    select(GitAccountRepositoryAccess).where(
                        GitAccountRepositoryAccess.account_connection_id == account.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for access in prior_access:
            if access.repository_id not in visible_ids:
                access.state = "lost"
        account.verification_status = "healthy"
        account.verified_at = now
        account.last_synced_at = now
        account.last_error = None
        account.revision += 1
        job.state = "succeeded"
        job.finished_at = now
    except (GitProviderError, ValueError) as exc:
        account.verification_status = "unavailable"
        account.last_error = exc.code if isinstance(exc, GitProviderError) else type(exc).__name__
        account.revision += 1
        job.state = "failed"
        job.failure_code = account.last_error
        job.finished_at = datetime.now(UTC)
    return job


@router.get("/git-adapters")
async def list_git_adapters(_: int = Depends(require_admin)):
    return [
        {
            "id": adapter.id,
            "display_name": adapter.display_name,
            "official_api_url": adapter.official_api_url,
            "custom_endpoint_allowed": adapter.custom_endpoint_allowed,
        }
        for adapter in registered_adapters()
    ]


@router.get("/git-accounts", response_model=list[GitAccountOut])
async def list_git_accounts(
    _: int = Depends(require_admin), session: AsyncSession = Depends(get_session)
):
    rows = (await session.execute(select(GitAccount).order_by(GitAccount.adapter_id, GitAccount.name))).scalars()
    return [await _git_account_out(session, row) for row in rows]


async def _create_git_account(
    session: AsyncSession, user: User, payload: GitAccountCreate
) -> GitAccountOut:
    try:
        adapter = require_adapter(payload.adapter_id)
        api_url = resolve_api_url(adapter.id, payload.api_url)
        profile = await authenticate_access_token(
            adapter_id=adapter.id, api_url=api_url, token=payload.access_token
        )
    except (GitProviderError, ValueError) as exc:
        code = exc.code if isinstance(exc, GitProviderError) else "git_account_configuration_invalid"
        raise _error(422, code, "Git account verification failed.") from exc
    account = GitAccount(
        adapter_id=adapter.id,
        api_url=api_url,
        endpoint_identity_hash=endpoint_identity_hash(adapter.id, api_url),
        name=payload.name.strip(),
        external_account_id=profile.external_id,
        external_account_login=profile.login,
        account_url=profile.account_url,
        state="active",
        verification_status="healthy",
        verified_at=datetime.now(UTC),
    )
    session.add(account)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(409, "git_account_conflict", "Git account already exists for this endpoint.") from exc
    username = "oauth2" if adapter.id in {"gitlab", "gitee"} else "x-access-token"
    await _append_account_credential(session, account, username=username, token=payload.access_token)
    job = await _sync_git_account(session, account)
    if job.state != "succeeded":
        await session.rollback()
        raise _error(502, "git_repository_sync_failed", "Git account repository sync failed.")
    session.add(_audit(user, "git_account.create", "git_account", account.id))
    await session.commit()
    await session.refresh(account)
    return await _git_account_out(session, account)


@router.post("/git-accounts", response_model=GitAccountOut, status_code=201)
async def create_git_account(
    payload: GitAccountCreate,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _create_git_account(session, await _active_user(session, user_id), payload)


@router.patch("/git-accounts/{account_id}", response_model=GitAccountOut)
async def patch_git_account(
    account_id: EntityId,
    payload: GitAccountPatch,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    account = await session.get(GitAccount, account_id)
    if account is None:
        raise _error(404, "git_account_not_found", "Git account was not found.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, key, value)
    account.revision += 1
    session.add(_audit(user, "git_account.update", "git_account", account.id))
    await session.commit()
    await session.refresh(account)
    return await _git_account_out(session, account)


@router.post("/git-accounts/{account_id}/access-token", response_model=GitAccountOut)
async def rotate_git_account_token(
    account_id: EntityId,
    payload: GitAccountTokenRotate,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    account = await session.get(GitAccount, account_id)
    if account is None:
        raise _error(404, "git_account_not_found", "Git account was not found.")
    try:
        profile = await authenticate_access_token(
            adapter_id=account.adapter_id, api_url=account.api_url, token=payload.access_token
        )
    except GitProviderError as exc:
        raise _error(422, exc.code, "Git account credential verification failed.") from exc
    if profile.external_id != account.external_account_id:
        raise _error(422, "git_account_identity_mismatch", "Access token belongs to another Git account.")
    username = "oauth2" if account.adapter_id in {"gitlab", "gitee"} else "x-access-token"
    await _append_account_credential(session, account, username=username, token=payload.access_token)
    job = await _sync_git_account(session, account)
    session.add(_audit(user, "git_account.token_rotate", "git_account", account.id))
    await session.commit()
    if job.state != "succeeded":
        raise _error(502, "git_repository_sync_failed", "Git account repository sync failed.")
    await session.refresh(account)
    return await _git_account_out(session, account)


@router.post("/git-accounts/{account_id}/sync", response_model=GitAccountOut)
async def sync_git_account(
    account_id: EntityId,
    user_id: EntityId = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await _active_user(session, user_id)
    account = await session.get(GitAccount, account_id)
    if account is None:
        raise _error(404, "git_account_not_found", "Git account was not found.")
    if account.state != "active":
        raise _error(409, "git_account_inactive", "An active Git account is required.")
    job = await _sync_git_account(session, account)
    session.add(_audit(user, "git_account.sync", "git_account", account.id))
    await session.commit()
    if job.state != "succeeded":
        raise _error(502, "git_repository_sync_failed", "Git account repository sync failed.")
    await session.refresh(account)
    return await _git_account_out(session, account)


@router.get("/git-accounts/{account_id}/repositories", response_model=list[GitAccountRepositoryOut])
async def list_git_account_repositories_current(
    account_id: EntityId,
    _: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(GitRepository)
            .join(GitAccountRepositoryAccess, GitAccountRepositoryAccess.repository_id == GitRepository.id)
            .where(
                GitAccountRepositoryAccess.account_connection_id == account_id,
                GitAccountRepositoryAccess.state == "available",
            )
            .order_by(GitRepository.full_name)
        )
    ).scalars()
    return [
        GitAccountRepositoryOut(
            repository_id=repository.id,
            provider_kind=repository.adapter_id,
            full_name=repository.full_name,
            repo_url=repository.repo_url,
            web_url=repository.web_url,
            default_branch=repository.default_branch,
            visibility=repository.visibility,
            archived=repository.archived,
        )
        for repository in rows
    ]


@router.get(
    "/git-accounts/{account_id}/repositories/{repository_id}/branches",
    response_model=GitBranchPageOut,
)
async def list_git_account_repository_branches(
    account_id: EntityId,
    repository_id: EntityId,
    cursor: str | None = Query(default=None, max_length=10),
    q: str | None = Query(default=None, min_length=1, max_length=128),
    _: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        page = int(cursor or "1")
    except ValueError as exc:
        raise _error(422, "invalid_branch_cursor", "Branch cursor is invalid.") from exc
    account = await session.get(GitAccount, account_id)
    repository = await session.get(GitRepository, repository_id)
    access = await session.get(GitAccountRepositoryAccess, (account_id, repository_id))
    if account is None or account.state != "active" or account.verification_status != "healthy":
        raise _error(422, "git_account_connection_invalid", "A healthy active Git account is required.")
    if repository is None or repository.archived:
        raise _error(404, "repository_not_found", "Repository was not found.")
    if access is None or access.state != "available":
        raise _error(409, "repository_access_lost", "Git account no longer has read access to this repository.")
    try:
        secret = await _account_secret(session, account)
        branches, next_cursor = await list_provider_branches(
            adapter_id=account.adapter_id,
            api_url=account.api_url,
            token=secret.token,
            external_repository_id=repository.external_repository_id,
            full_name=repository.full_name,
            page=page,
            query=q,
        )
    except GitProviderError as exc:
        raise _error(502, f"git_branch_catalog_{exc.code}", "Git branches could not be loaded.") from exc
    return GitBranchPageOut(
        items=[GitBranchOut(name=branch.name, is_default=branch.name == repository.default_branch) for branch in branches],
        next_cursor=next_cursor,
    )


@router.get("/workspaces/{workspace_id}/repositories", response_model=list[RepositoryBindingOut])
async def list_repositories(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    rows = (
        await session.execute(
            select(WorkspaceRepositoryBinding, GitRepository, GitAccount)
            .join(GitRepository, GitRepository.id == WorkspaceRepositoryBinding.repository_id)
            .join(GitAccount, GitAccount.id == WorkspaceRepositoryBinding.account_connection_id)
            .where(WorkspaceRepositoryBinding.workspace_id == workspace_id)
            .order_by(WorkspaceRepositoryBinding.priority, WorkspaceRepositoryBinding.id)
        )
    ).all()
    return [_repository_out(binding, repository, account) for binding, repository, account in rows]


async def _create_repository_binding(session, workspace_id, user, account, repository, payload):
    row = WorkspaceRepositoryBinding(
        workspace_id=workspace_id,
        repository_id=repository.id,
        account_connection_id=account.id,
        role=payload.role,
        branch_mode=payload.branch_mode,
        branch_name=payload.branch_name,
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
    return _repository_out(row, repository, account)


async def _validate_fixed_branch(
    session: AsyncSession,
    account: GitAccount,
    repository: GitRepository,
    branch_mode: str,
    branch_name: str | None,
) -> None:
    if branch_mode == "default":
        return
    assert branch_name is not None
    try:
        secret = await _account_secret(session, account)
        exists = await verify_provider_branch(
            adapter_id=account.adapter_id,
            api_url=account.api_url,
            token=secret.token,
            external_repository_id=repository.external_repository_id,
            full_name=repository.full_name,
            branch_name=branch_name,
        )
    except GitProviderError as exc:
        raise _error(502, f"git_branch_catalog_{exc.code}", "Git branch could not be verified.") from exc
    if not exists:
        raise _error(422, "repository_branch_not_found", "The selected branch is not available.")


@router.post(
    "/workspaces/{workspace_id}/repositories", response_model=RepositoryBindingOut, status_code=201
)
async def bind_repository(
    workspace_id: EntityId,
    payload: RepositoryBind,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    account = await session.get(GitAccount, payload.account_connection_id)
    repository = await session.get(GitRepository, payload.repository_id)
    if account is None or account.state != "active" or account.verification_status != "healthy":
        raise _error(422, "git_account_connection_invalid", "A healthy active Git account is required.")
    if repository is None or repository.archived:
        raise _error(404, "repository_not_found", "Repository was not found.")
    access = await session.get(
        GitAccountRepositoryAccess, (account.id, repository.id)
    )
    if access is None or access.state != "available":
        raise _error(409, "repository_access_lost", "Git account no longer has read access to this repository.")
    await _validate_fixed_branch(
        session, account, repository, payload.branch_mode, payload.branch_name
    )
    return await _create_repository_binding(session, workspace_id, user, account, repository, payload)


@router.patch(
    "/workspaces/{workspace_id}/repositories/{binding_id}", response_model=RepositoryBindingOut
)
async def patch_repository_binding(
    workspace_id: EntityId,
    binding_id: EntityId,
    payload: RepositoryBindingPatch,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(WorkspaceRepositoryBinding, binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "repository_binding_not_found", "Repository binding not found.")
    if row.revision != payload.expected_revision:
        raise _error(
            409,
            "repository_binding_revision_conflict",
            "Repository binding changed. Reload and try again.",
            current_revision=row.revision,
        )
    values = payload.model_dump(exclude_unset=True)
    values.pop("expected_revision")
    branch_mode = values.get("branch_mode", row.branch_mode)
    branch_name = values.get("branch_name", row.branch_name)
    if branch_mode == "default":
        branch_name = None
    elif not branch_name:
        raise _error(422, "repository_branch_required", "A fixed branch must be selected.")
    repository = await session.get(GitRepository, row.repository_id)
    account = await session.get(GitAccount, row.account_connection_id)
    assert repository is not None and account is not None
    if branch_mode == "branch" and (branch_mode != row.branch_mode or branch_name != row.branch_name):
        await _validate_fixed_branch(session, account, repository, branch_mode, branch_name)
    structural_change = (
        values.get("role", row.role) != row.role
        or branch_mode != row.branch_mode
        or branch_name != row.branch_name
        or values.get("state", row.state) != row.state
    )
    values["branch_mode"] = branch_mode
    values["branch_name"] = branch_name
    for key, value in values.items():
        setattr(row, key, value)
    if structural_change:
        row.descriptor_revision += 1
    row.revision += 1
    session.add(
        _audit(
            user, "repository_binding.update", "workspace_repository_binding", row.id, workspace_id
        )
    )
    await session.commit()
    await session.refresh(row)
    return _repository_out(row, repository, account)


@router.delete("/workspaces/{workspace_id}/repositories/{binding_id}", status_code=204)
async def disable_repository_binding(
    workspace_id: EntityId,
    binding_id: EntityId,
    expected_revision: int = Query(gt=0),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    row = await session.get(WorkspaceRepositoryBinding, binding_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "repository_binding_not_found", "Repository binding not found.")
    if row.revision != expected_revision:
        raise _error(
            409,
            "repository_binding_revision_conflict",
            "Repository binding changed. Reload and try again.",
            current_revision=row.revision,
        )
    if row.state == "disabled":
        raise _error(409, "repository_binding_disabled", "Repository binding is already disabled.")
    row.state = "disabled"
    row.descriptor_revision += 1
    row.revision += 1
    session.add(
        _audit(
            user, "repository_binding.disable", "workspace_repository_binding", row.id, workspace_id
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.get(
    "/workspaces/{workspace_id}/repository-analysis",
    response_model=RepositoryAnalysisJobOut | None,
)
async def get_repository_analysis(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    row = (
        await session.execute(
            select(RepositoryAnalysisJob)
            .where(RepositoryAnalysisJob.workspace_id == workspace_id)
            .order_by(RepositoryAnalysisJob.created_at.desc(), RepositoryAnalysisJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return await _repository_analysis_out(session, row) if row is not None else None


@router.post(
    "/workspaces/{workspace_id}/repository-analysis",
    response_model=RepositoryAnalysisJobOut,
    status_code=202,
)
async def start_repository_analysis(
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    active = (
        await session.execute(
            select(RepositoryAnalysisJob)
            .where(
                RepositoryAnalysisJob.workspace_id == workspace_id,
                RepositoryAnalysisJob.state.in_(("queued", "running")),
            )
            .order_by(RepositoryAnalysisJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        raise _error(
            409,
            "repository_analysis_in_progress",
            "A repository analysis is already in progress.",
            job_id=active.id,
        )
    binding_snapshot, input_hash = await _analysis_input(session, workspace_id)
    if not binding_snapshot:
        raise _error(
            409,
            "repository_analysis_requires_repository",
            "Bind at least one active repository before starting analysis.",
        )
    row = RepositoryAnalysisJob(
        workspace_id=workspace_id,
        requested_binding_ids=[item["binding_id"] for item in binding_snapshot],
        binding_snapshot=binding_snapshot,
        input_hash=input_hash,
        requested_by=user.id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _error(
            409,
            "repository_analysis_in_progress",
            "A repository analysis is already in progress.",
        ) from exc
    session.add(
        _audit(user, "repository_analysis.start", "repository_analysis_job", row.id, workspace_id)
    )
    await session.commit()
    await session.refresh(row)
    return RepositoryAnalysisJobOut.model_validate(row)


@router.get(
    "/workspaces/{workspace_id}/repository-analysis/{job_id}/issues",
    response_model=RepositoryAnalysisIssuePageOut,
)
async def list_repository_analysis_issues(
    workspace_id: EntityId,
    job_id: EntityId,
    after: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await _workspace_access(session, user_id, workspace_id, "read")
    job = await session.get(RepositoryAnalysisJob, job_id)
    if job is None or job.workspace_id != workspace_id:
        raise _error(404, "repository_analysis_not_found", "Repository analysis was not found.")
    statement = (
        select(RepositoryAnalysisIssue)
        .where(RepositoryAnalysisIssue.repository_analysis_job_id == job_id)
        .order_by(RepositoryAnalysisIssue.ordinal)
        .limit(limit + 1)
    )
    if after is not None:
        statement = statement.where(RepositoryAnalysisIssue.ordinal > after)
    rows = list((await session.execute(statement)).scalars())
    page = rows[:limit]
    return RepositoryAnalysisIssuePageOut(
        items=[RepositoryAnalysisIssueOut.model_validate(row) for row in page],
        next_cursor=page[-1].ordinal if len(rows) > limit and page else None,
    )


_CONNECTOR_SECRET_FIELDS = {
    "loki": ["bearer_token"],
    "elasticsearch": ["api_key", "bearer_token", "username", "password"],
    "opensearch": ["api_key", "bearer_token", "username", "password"],
    "postgresql": ["password"],
    "mysql": ["password"],
    "https": ["api_key", "bearer_token", "username", "password"],
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
    public_config = {
        key: value for key, value in row.config.items() if key != "ca_certificate_pem"
    }
    return ConnectorOut(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        kind=row.kind,
        kind_version=row.kind_version,
        config=public_config,
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
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
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


def _connector_secrets(payload: ConnectorCreate) -> dict[str, str]:
    if payload.authentication == "bearer_token":
        return {"bearer_token": payload.credential or ""}
    if payload.authentication == "api_key":
        return {"api_key": payload.credential or ""}
    if payload.authentication == "basic":
        return {"username": payload.credential_username or "", "password": payload.credential or ""}
    return {}


def _connector_storage(payload: ConnectorCreate) -> tuple[dict, dict[str, str], dict, dict]:
    budget = {"timeout_ms": 5_000, "max_rows": 1_000, "max_output_bytes": 1_000_000}
    if payload.kind == "loki":
        if payload.root_filter is None:
            raise ValueError("Loki root filter is required")
        root_filter = payload.root_filter.model_dump()
        branches = normalize_loki_filter(root_filter)
        return (
            {"base_url": payload.endpoint, **({"tenant_id": payload.tenant_id} if payload.tenant_id else {})},
            {"bearer_token": payload.credential} if payload.authentication == "bearer_token" else {},
            {"root_filter": root_filter, "root_filter_dnf": [[dict(item) for item in branch] for branch in branches]},
            budget,
        )
    if payload.kind in {"elasticsearch", "opensearch"}:
        indices = list(payload.allowed_indices)
        return (
            {"base_url": payload.endpoint},
            _connector_secrets(payload),
            {"allowed_indices": indices, "cardinality_bounds": {}},
            budget,
        )
    if payload.kind == "https":
        parsed = urlsplit(payload.endpoint or "")
        try:
            default_port = 80 if parsed.scheme == "http" else 443
            port = parsed.port or default_port
        except ValueError as exc:
            raise ValueError("HTTP connector port is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("HTTP connector endpoint must be an HTTP or HTTPS origin")
        return (
            {"base_url": payload.endpoint, "verification_path": payload.verification_path or "/health"},
            _connector_secrets(payload),
            {
                "safe_read_endpoints": [{
                    "id": "default-read",
                    "method": "GET",
                    "scheme": parsed.scheme,
                    "host": parsed.hostname.lower(),
                    "port": port,
                    "path_template": payload.safe_read_path,
                    "path_parameters": {},
                    "query_parameters": {},
                    "allowed_content_types": ["application/json"],
                    "max_response_bytes": 1_000_000,
                }]
            },
            budget,
        )
    if payload.kind in {"postgresql", "mysql"}:
        allowed_schemas = (
            {"allowed_schemas": list(payload.allowed_schemas)}
            if payload.kind == "postgresql"
            else {}
        )
        return (
            {
                "host": payload.host,
                "port": payload.port or (5432 if payload.kind == "postgresql" else 3306),
                "database": payload.database,
                "username": payload.database_username,
                "tls_mode": payload.tls_mode,
                **(
                    {"ca_certificate_pem": payload.ca_certificate_pem}
                    if payload.ca_certificate_pem is not None
                    else {}
                ),
            },
            {"password": payload.database_password or ""},
            {
                **allowed_schemas,
                "allowed_tables": [],
                "table_policies": {},
            },
            budget,
        )
    raise ValueError("unsupported connector kind")


def _connector_introspection_budget(kind: str, now: datetime) -> IntrospectionBudget:
    return IntrospectionBudget(
        timeout_ms=10_000 if kind in {"postgresql", "mysql"} else 5_000,
        max_resources=500,
        window_start=now - timedelta(minutes=30),
        window_end=now,
    )


_CONNECTOR_PROVIDER_ERROR_STATUS = {
    "authentication_failed": 422,
    "unsupported_version": 422,
    "provider_timeout": 504,
}
_CONNECTOR_PROVIDER_DETAIL_FIELDS = frozenset(
    {
        "provider",
        "observed_version",
        "supported_major_versions",
        "status_code",
        "failed_checks",
        "sqlstate",
    }
)


def _connector_operation_error(
    operation: str, exc: Exception, fallback_message: str
) -> HTTPException:
    if not isinstance(exc, ProviderExecutionError):
        return _error(502, f"connector_{operation}_failed", fallback_message)
    safe_details = {
        key: value
        for key, value in exc.detail.items()
        if key in _CONNECTOR_PROVIDER_DETAIL_FIELDS
    }
    return _error(
        _CONNECTOR_PROVIDER_ERROR_STATUS.get(exc.code, 502),
        f"connector_{operation}_{exc.code}",
        exc.reason,
        provider_error=exc.code,
        **safe_details,
    )


def _scope_config_from_catalog(
    kind: str, current_scope: dict, resources: dict
) -> dict:
    if kind not in {"postgresql", "mysql"}:
        return current_scope
    tables = resources.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("SQL discovery returned an invalid catalog")
    if any(
        not isinstance(table, str)
        or not isinstance(descriptor, dict)
        or not isinstance(descriptor.get("time_column"), str)
        or not isinstance(descriptor.get("stable_order"), list)
        or not descriptor["stable_order"]
        for table, descriptor in tables.items()
    ):
        raise ValueError("SQL discovery returned an invalid catalog")
    allowed_schemas = {}
    if kind == "postgresql":
        schemas = current_scope.get("allowed_schemas")
        if not isinstance(schemas, list) or not schemas:
            raise ValueError("PostgreSQL Schema allowlist is required")
        allowed_schemas = {"allowed_schemas": list(schemas)}
    return {
        **allowed_schemas,
        "allowed_tables": sorted(tables),
        "table_policies": {
            table: {
                "time_column": descriptor["time_column"],
                "stable_order": descriptor["stable_order"],
            }
            for table, descriptor in tables.items()
        },
    }


@router.post(
    "/workspaces/{workspace_id}/evidence-connectors", response_model=ConnectorOut, status_code=201
)
async def create_connector(
    workspace_id: EntityId,
    payload: ConnectorCreate,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    user, _ = await _workspace_access(session, user_id, workspace_id, "admin")
    duplicate = await session.scalar(
        select(EvidenceConnector.id).where(
            EvidenceConnector.workspace_id == workspace_id,
            EvidenceConnector.name == payload.name,
        )
    )
    if duplicate is not None:
        raise _error(409, "connector_name_conflict", "Connector name is already used.")
    actor_id, actor_username = user.id, user.username
    # Do not keep the authorization read transaction open across remote I/O.
    await session.rollback()
    try:
        config, secrets, scope_config, budget = _connector_storage(payload)
        adapter = create_evidence_connector(payload.kind, config, secrets)
    except ValueError as exc:
        raise _error(422, "connector_configuration_invalid", str(exc)) from exc
    try:
        await adapter.verify()
    except Exception as exc:
        raise _connector_operation_error(
            "verification", exc, "Read-only connector verification failed."
        ) from exc
    now = datetime.now(UTC)
    try:
        catalog = await adapter.introspect(
            scope_config, _connector_introspection_budget(payload.kind, now)
        )
        final_scope_config = _scope_config_from_catalog(
            payload.kind, scope_config, dict(catalog.resources)
        )
    except Exception as exc:
        raise _connector_operation_error(
            "introspection", exc, "Connector scope discovery failed."
        ) from exc
    if payload.kind in {"postgresql", "mysql"} and not final_scope_config["allowed_tables"]:
        raise _error(
            422,
            "connector_scope_empty",
            "No safely queryable tables were found in the configured database scope.",
        )
    language, capabilities = _connector_capability(payload.kind)
    ciphertext = (
        encrypt_secret(
            json.dumps(secrets, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        or ""
    )
    row = EvidenceConnector(
        workspace_id=workspace_id,
        name=payload.name,
        kind=payload.kind,
        kind_version=2 if payload.kind == "https" else 1,
        config=config,
        secret_ciphertext=ciphertext,
        instance_revision=1,
        capabilities=capabilities,
        verification_status="healthy",
        verified_at=now,
        last_error=None,
        last_introspected_at=now,
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
            scope_config=final_scope_config,
            schema_catalog=dict(catalog.resources),
            schema_catalog_revision=1,
            read_policy_revision=1,
            execution_budget_policy=budget,
            normalization_policy_revision=1,
            revision=1,
        )
    )
    session.add(
        AuditEvent(
            actor_id=actor_id,
            actor_username=actor_username,
            action="evidence_connector.create",
            target_type="evidence_connector",
            target_id=str(row.id),
            workspace_id=workspace_id,
            result="ok",
            detail={},
        )
    )
    await session.commit()
    await session.refresh(row)
    return _connector_out(row)


async def _latest_scope(session: AsyncSession, connector_id: EntityId) -> EvidenceAccessScope:
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


@router.post("/workspaces/{workspace_id}/evidence-connectors/{connector_id}/test")
async def test_connector(
    workspace_id: EntityId,
    connector_id: EntityId,
    user_id: EntityId = Depends(require_user),
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
        raise _connector_operation_error(
            "verification", exc, "Read-only connector verification failed."
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
    workspace_id: EntityId,
    connector_id: EntityId,
    user_id: EntityId = Depends(require_user),
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
    try:
        catalog = await adapter.introspect(
            scope.scope_config, _connector_introspection_budget(row.kind, now)
        )
    except Exception as exc:
        raise _connector_operation_error(
            "introspection", exc, "Connector scope discovery failed."
        ) from exc
    try:
        scope_config = _scope_config_from_catalog(
            row.kind, scope.scope_config, dict(catalog.resources)
        )
    except ValueError as exc:
        raise _error(
            502,
            "connector_introspection_invalid",
            "SQL discovery returned an invalid catalog.",
        ) from exc
    new_scope = EvidenceAccessScope(
        connector_id=row.id,
        allowed_languages=scope.allowed_languages,
        scope_config=scope_config,
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
        "readiness": "ready"
        if row.kind not in {"postgresql", "mysql"} or bool(scope_config["allowed_tables"])
        else "empty",
    }


@router.delete("/workspaces/{workspace_id}/evidence-connectors/{connector_id}", status_code=204)
async def disable_connector(
    workspace_id: EntityId,
    connector_id: EntityId,
    user_id: EntityId = Depends(require_user),
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
