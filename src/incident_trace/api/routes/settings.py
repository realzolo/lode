"""Global settings read endpoint.

Returns the platform-wide configuration the Settings UI renders: the global
read-only git account, the repository registry, and the AI model
configuration(s). Secrets (``secret_ref`` / ``api_key_ref``) are never exposed
— only non-sensitive metadata is returned.
"""

from __future__ import annotations

from fastapi import APIRouter

from incident_trace.db.models.ai_model import AiModelConfig
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
                "model": m.model,
                "is_default": m.is_default,
                "has_key": bool(m.api_key_ref),
            }
            for m in models
        ],
    }
