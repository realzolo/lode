"""Global settings read endpoint + admin AI-model configuration CRUD.

``GET /settings`` (any authenticated user) returns the platform-wide read-only
configuration the Settings UI renders. The AI-model configuration write
endpoints (``POST/PUT/DELETE /settings/ai-models``) are **admin only** and let
operators register OpenAI-/Anthropic-compatible endpoints.

Secrets (``api_key_ref``) are never returned to the client — only non-sensitive
metadata. We support ``env://NAME`` references so the real credential stays in
the deployment environment and never lands in the database row.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from incident_trace.api.deps import require_admin
from incident_trace.api.schemas import (
    AiModelConfigIn,
    AiModelConfigOut,
)
from incident_trace.db.models.ai_model import AiModelConfig
from incident_trace.db.models.application import Application
from incident_trace.db.models.git import GitCredential, GitRepo
from incident_trace.db.session import AsyncSessionLocal
from sqlalchemy import select

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings() -> dict:
    async with AsyncSessionLocal() as session:
        creds = (await session.execute(select(GitCredential))).scalars().all()
        repos = (await session.execute(select(GitRepo))).scalars().all()
        models = (await session.execute(select(AiModelConfig))).scalars().all()

    return {
        "git_credentials": [
            {
                "id": c.id,
                "auth_type": c.auth_type,
                "username": c.username,
                "readonly": c.readonly,
                "note": c.note,
                "has_secret": bool(c.secret_ref),
            }
            for c in creds
        ],
        "git_repos": [
            {"id": r.id, "name": r.name, "repo_url": r.repo_url, "default_branch": r.default_branch}
            for r in repos
        ],
        "ai_model_configs": [
            {
                "id": m.id,
                "scope": m.scope,
                "application_id": m.application_id,
                "provider": m.provider,
                "base_url": m.base_url,
                "model": m.model,
                "is_default": m.is_default,
                "has_key": bool(m.api_key_ref),
            }
            for m in models
        ],
    }


def _row_to_out(m: AiModelConfig) -> AiModelConfigOut:
    return AiModelConfigOut(
        id=m.id,
        scope=m.scope,
        application_id=m.application_id,
        provider=m.provider,
        base_url=m.base_url,
        model=m.model,
        is_default=m.is_default,
        has_key=bool(m.api_key_ref),
    )


async def _enforce_single_default(
    session, scope: str, application_id: int | None, keep_id: int | None
) -> None:
    """Clear ``is_default`` on every other config sharing the same scope.

    When ``keep_id`` is ``None`` (used before inserting a brand-new default),
    all existing rows in the scope are demoted. This MUST happen *before* the
    new/updated row is set to ``is_default=True`` so the partial unique index
    (at most one default per scope) is never violated mid-transaction.
    """
    stmt = select(AiModelConfig).where(AiModelConfig.scope == scope)
    if keep_id is not None:
        stmt = stmt.where(AiModelConfig.id != keep_id)
    if scope == "application":
        stmt = stmt.where(AiModelConfig.application_id == application_id)
    others = (await session.execute(stmt)).scalars().all()
    for other in others:
        other.is_default = False


async def _promote_if_no_default(
    session, scope: str, application_id: int | None
) -> None:
    """If a scope has configs but no default, promote the newest one.

    Keeps the engine's ``_resolve_model_config`` functional instead of silently
    falling back to the heuristic when an operator forgets to tick "default".
    """
    existing = (
        await session.execute(
            select(AiModelConfig)
            .where(AiModelConfig.scope == scope)
            .order_by(AiModelConfig.id.desc())
        )
    ).scalars().all()
    if not existing:
        return
    if any(m.is_default for m in existing):
        return
    existing[0].is_default = True


@router.get("/ai-models", response_model=list[AiModelConfigOut])
async def list_ai_models(_admin: int = Depends(require_admin)) -> list[AiModelConfigOut]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AiModelConfig).order_by(AiModelConfig.id))).scalars().all()
    return [_row_to_out(m) for m in rows]


@router.post("/ai-models", response_model=AiModelConfigOut, status_code=201)
async def create_ai_model(
    payload: AiModelConfigIn, _admin: int = Depends(require_admin)
) -> AiModelConfigOut:
    async with AsyncSessionLocal() as session:
        if payload.scope == "application":
            if payload.application_id is None:
                raise HTTPException(status_code=422, detail="application_id is required for scope=application")
            app = await session.get(Application, payload.application_id)
            if app is None:
                raise HTTPException(status_code=404, detail="application not found")
        else:
            payload.application_id = None

        if not payload.api_key_ref:
            raise HTTPException(status_code=422, detail="api_key_ref is required")

        # If this becomes the default, demote any existing default in the scope
        # *before* the insert, so the one-default-per-scope partial index is
        # never violated mid-transaction.
        if payload.is_default:
            await _enforce_single_default(session, payload.scope, payload.application_id, None)
            await session.flush()

        model = AiModelConfig(
            scope=payload.scope,
            application_id=payload.application_id,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key_ref=payload.api_key_ref,
            model=payload.model,
            is_default=payload.is_default,
        )
        session.add(model)
        await session.flush()
        if not payload.is_default:
            # Auto-promote to default if the scope has no default yet.
            await _promote_if_no_default(session, payload.scope, payload.application_id)
        await session.commit()
        await session.refresh(model)
        return _row_to_out(model)


@router.put("/ai-models/{model_id}", response_model=AiModelConfigOut)
async def update_ai_model(
    model_id: int, payload: AiModelConfigIn, _admin: int = Depends(require_admin)
) -> AiModelConfigOut:
    async with AsyncSessionLocal() as session:
        model = await session.get(AiModelConfig, model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model config not found")

        if payload.scope == "application":
            if payload.application_id is None:
                raise HTTPException(status_code=422, detail="application_id is required for scope=application")
            app = await session.get(Application, payload.application_id)
            if app is None:
                raise HTTPException(status_code=404, detail="application not found")
        else:
            payload.application_id = None

        model.scope = payload.scope
        model.application_id = payload.application_id
        model.provider = payload.provider
        model.base_url = payload.base_url
        # Only overwrite the secret when a non-empty value is supplied, so
        # operators can update metadata without re-pasting the key.
        if payload.api_key_ref:
            model.api_key_ref = payload.api_key_ref
        model.model = payload.model

        if payload.is_default:
            # Demote other defaults in the scope first (flush), then promote
            # this row — keeps the one-default-per-scope index satisfied.
            await _enforce_single_default(session, payload.scope, payload.application_id, model.id)
            await session.flush()
            model.is_default = True
        elif model.is_default:
            # Demoting the current default: promote another in the same scope.
            model.is_default = False
            await _promote_if_no_default(session, payload.scope, payload.application_id)
        else:
            model.is_default = False

        await session.commit()
        await session.refresh(model)
        return _row_to_out(model)


@router.delete("/ai-models/{model_id}", status_code=204)
async def delete_ai_model(model_id: int, _admin: int = Depends(require_admin)) -> None:
    async with AsyncSessionLocal() as session:
        model = await session.get(AiModelConfig, model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="model config not found")
        scope = model.scope
        application_id = model.application_id
        was_default = model.is_default
        await session.delete(model)
        await session.flush()
        # If we removed the only default, promote a replacement so the engine
        # keeps working.
        if was_default:
            await _promote_if_no_default(session, scope, application_id)
        await session.commit()
