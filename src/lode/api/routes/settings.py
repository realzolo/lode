"""Global settings read endpoint + admin AI-model configuration CRUD.

``GET /settings`` (any authenticated user) returns the platform-wide read-only
configuration the Settings UI renders. The AI-model configuration write
endpoints (``POST/PUT/DELETE /settings/ai-models``) are **admin only** and let
operators register OpenAI-/Anthropic-compatible endpoints.

Secrets (``api_key_ref``) are never returned to the client — only non-sensitive
metadata. We support ``env://NAME`` references so the real credential stays in
the deployment environment and never lands in the database row. A literal key
supplied directly is encrypted at rest (Fernet, keyed off ``secret_key``) so the
plaintext never persists in the ``ai_model_configs`` table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lode.api.deps import require_admin
from lode.api.schemas import (
    AiModelConfigIn,
    AiModelConfigOut,
    GitCredentialIn,
    GitCredentialOut,
    GitCredentialUpdateIn,
    GitRepoIn,
    GitRepoOut,
    GitRepoUpdateIn,
)
from lode.crypto import encrypt_secret
from lode.db.models.ai_model import AiModelConfig
from lode.db.models.application import Application
from lode.db.models.git import GitCredential, GitRepo
from lode.db.session import AsyncSessionLocal
from sqlalchemy import select


def _store_key_ref(api_key_ref: str) -> str:
    """Normalize an ``api_key_ref`` for storage.

    ``env://NAME`` references are kept verbatim. A literal key is encrypted with
    :func:`encrypt_secret` so the plaintext never lands in the database row.
    """
    if api_key_ref.startswith("env://"):
        return api_key_ref
    return encrypt_secret(api_key_ref) or ""

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
            {
                "id": r.id,
                "name": r.name,
                "repo_url": r.repo_url,
                "default_branch": r.default_branch,
                "repo_type": r.repo_type,
                "credential_id": r.credential_id,
            }
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

    For ``scope='application'`` the search is limited to the same
    ``application_id`` so a replacement default is promoted *within the correct
    application* and never accidentally borrowed from a sibling application.
    """
    stmt = select(AiModelConfig).where(AiModelConfig.scope == scope)
    if scope == "application":
        stmt = stmt.where(AiModelConfig.application_id == application_id)
    existing = (
        await session.execute(stmt.order_by(AiModelConfig.id.desc()))
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
            api_key_ref=_store_key_ref(payload.api_key_ref),
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
        # operators can update metadata without re-pasting the key. A literal
        # value is re-encrypted at rest; an ``env://`` reference is kept as-is.
        if payload.api_key_ref:
            model.api_key_ref = _store_key_ref(payload.api_key_ref)
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


# ---------------------------------------------------------------------------
# Git credentials (admin only)
# ---------------------------------------------------------------------------

def _cred_to_out(c: GitCredential) -> GitCredentialOut:
    return GitCredentialOut(
        id=c.id,
        auth_type=c.auth_type,
        username=c.username,
        readonly=c.readonly,
        note=c.note,
        has_secret=bool(c.secret_ref),
    )


@router.post("/git-credentials", response_model=GitCredentialOut, status_code=201)
async def create_git_credential(
    payload: GitCredentialIn, _admin: int = Depends(require_admin)
) -> GitCredentialOut:
    async with AsyncSessionLocal() as session:
        cred = GitCredential(
            auth_type=payload.auth_type,
            username=payload.username,
            # Encrypt a literal secret at rest; keep ``env://`` references
            # verbatim so the real credential stays in the deployment env.
            secret_ref=_store_key_ref(payload.secret_ref),
            readonly=payload.readonly,
            note=payload.note,
        )
        session.add(cred)
        await session.commit()
        await session.refresh(cred)
        return _cred_to_out(cred)


@router.put("/git-credentials/{cred_id}", response_model=GitCredentialOut)
async def update_git_credential(
    cred_id: int, payload: GitCredentialUpdateIn, _admin: int = Depends(require_admin)
) -> GitCredentialOut:
    async with AsyncSessionLocal() as session:
        cred = await session.get(GitCredential, cred_id)
        if cred is None:
            raise HTTPException(status_code=404, detail="git credential not found")
        if payload.auth_type is not None:
            cred.auth_type = payload.auth_type
        if payload.username is not None:
            cred.username = payload.username
        # Only overwrite the secret when a non-empty value is supplied, so
        # operators can rotate metadata without re-pasting the credential.
        if payload.secret_ref:
            cred.secret_ref = _store_key_ref(payload.secret_ref)
        if payload.readonly is not None:
            cred.readonly = payload.readonly
        if payload.note is not None:
            cred.note = payload.note
        await session.commit()
        await session.refresh(cred)
        return _cred_to_out(cred)


@router.delete("/git-credentials/{cred_id}", status_code=204)
async def delete_git_credential(cred_id: int, _admin: int = Depends(require_admin)) -> None:
    async with AsyncSessionLocal() as session:
        cred = await session.get(GitCredential, cred_id)
        if cred is None:
            raise HTTPException(status_code=404, detail="git credential not found")
        # Repositories pointing at this credential use ``ondelete="SET NULL"``
        # at the FK level, so their ``credential_id`` is cleared automatically
        # when the row is deleted.
        await session.delete(cred)
        await session.commit()


# ---------------------------------------------------------------------------
# Git repository registry (admin only)
# ---------------------------------------------------------------------------

def _repo_to_out(r: GitRepo) -> GitRepoOut:
    return GitRepoOut(
        id=r.id,
        name=r.name,
        repo_url=r.repo_url,
        default_branch=r.default_branch,
        repo_type=r.repo_type,
        credential_id=r.credential_id,
    )


async def _assert_credential(session, credential_id: int | None) -> None:
    """Validate a ``credential_id`` reference if one is supplied.

    A ``None`` value is always allowed (the repo simply has no bound account).
    A non-existent id is rejected with 404 so we never store a dangling FK.
    """
    if credential_id is None:
        return
    cred = await session.get(GitCredential, credential_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="git credential not found")


@router.post("/git-repos", response_model=GitRepoOut, status_code=201)
async def create_git_repo(
    payload: GitRepoIn, _admin: int = Depends(require_admin)
) -> GitRepoOut:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(GitRepo).where(GitRepo.repo_url == payload.repo_url)
            )
        ).scalars().first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="repo_url already registered")
        await _assert_credential(session, payload.credential_id)
        repo = GitRepo(
            name=payload.name,
            repo_url=payload.repo_url,
            default_branch=payload.default_branch,
            repo_type=payload.repo_type,
            credential_id=payload.credential_id,
        )
        session.add(repo)
        await session.commit()
        await session.refresh(repo)
        return _repo_to_out(repo)


@router.put("/git-repos/{repo_id}", response_model=GitRepoOut)
async def update_git_repo(
    repo_id: int, payload: GitRepoUpdateIn, _admin: int = Depends(require_admin)
) -> GitRepoOut:
    async with AsyncSessionLocal() as session:
        repo = await session.get(GitRepo, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="git repo not found")
        if payload.repo_url is not None and payload.repo_url != repo.repo_url:
            colliding = (
                await session.execute(
                    select(GitRepo).where(GitRepo.repo_url == payload.repo_url)
                )
            ).scalars().first()
            if colliding is not None and colliding.id != repo.id:
                raise HTTPException(status_code=409, detail="repo_url already registered")
        if payload.name is not None:
            repo.name = payload.name
        if payload.repo_url is not None:
            repo.repo_url = payload.repo_url
        if payload.default_branch is not None:
            repo.default_branch = payload.default_branch
        if payload.repo_type is not None:
            repo.repo_type = payload.repo_type
        # ``credential_id`` may be cleared (set to ``None``) to unbind, or
        # pointed at a different existing credential.
        if payload.credential_id is not None or "credential_id" in payload.model_fields_set:
            await _assert_credential(session, payload.credential_id)
            repo.credential_id = payload.credential_id
        await session.commit()
        await session.refresh(repo)
        return _repo_to_out(repo)


@router.delete("/git-repos/{repo_id}", status_code=204)
async def delete_git_repo(repo_id: int, _admin: int = Depends(require_admin)) -> None:
    async with AsyncSessionLocal() as session:
        repo = await session.get(GitRepo, repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="git repo not found")
        await session.delete(repo)
        await session.commit()
