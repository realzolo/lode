"""Controlled tools available to the analysis agent.

Every tool here is *read-only* and pulls its context from the platform's own
database (repository registry, preset prompts, table whitelist, shared
memory). They are the safe, auditable surface the agent is allowed to use —
no arbitrary shell, no write access to production data.

In a later phase ``run_readonly_query`` will proxy to the real read-only
replica. For now it returns the whitelisted tables so the agent (and the UI)
can see exactly what it would be permitted to read.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from lode.db.models.alert import Alert
from lode.db.models.application import (
    ApplicationRepo,
    DbSource,
    PresetPrompt,
)
from lode.db.models.git import GitRepo
from lode.db.models.memory import Memory
from lode.engine.db_proxy import DbProxyError, DbConnector, execute_query


async def search_code(session, application_id: int) -> dict[str, Any]:
    """List the repositories/modules the agent is allowed to inspect."""
    result = await session.execute(
        select(ApplicationRepo, GitRepo)
        .join(GitRepo, GitRepo.id == ApplicationRepo.repo_id)
        .where(ApplicationRepo.application_id == application_id)
    )
    rows = result.all()
    repos = [
        {
            "name": repo.name,
            "url": repo.repo_url,
            "description": app_repo.description,
        }
        for app_repo, repo in rows
    ]
    return {
        "repos": repos,
        "modules_searched": [r["description"] or r["name"] for r in repos],
        "note": "Source is inspected via the read-only repository registry.",
    }


async def get_deploy_context(session, application_id: int) -> dict[str, Any]:
    """Return the deployment description the team pre-configured for this app."""
    result = await session.execute(
        select(PresetPrompt)
        .where(PresetPrompt.application_id == application_id)
        .where(PresetPrompt.type == "deploy")
        .order_by(PresetPrompt.updated_at.desc())
    )
    prompt = result.scalars().first()
    return {
        "deploy_prompt": prompt.content if prompt else None,
        "note": "Deployment context sourced from preset prompts (type=deploy).",
    }


async def run_readonly_query(
    session,
    application_id: int,
    *,
    sql: str | None = None,
    source_id: int | None = None,
    connector: DbConnector | None = None,
    desensitize: bool = True,
) -> dict[str, Any]:
    """Read-only proxy for an application's whitelisted data sources.

    With no ``sql`` this returns the allow-list (used to brief the analysis
    agent about what it may read). When ``sql`` is provided the statement is
    validated against the chosen source's ``allowed_tables`` and executed
    read-only against the resolved replica, with sensitive columns masked.
    """
    result = await session.execute(
        select(DbSource).where(DbSource.application_id == application_id)
    )
    sources = result.scalars().all()
    allowed: list[str] = []
    for src in sources:
        tables = src.allowed_tables or []
        if isinstance(tables, list):
            allowed.extend(str(t) for t in tables)

    if sql is None:
        return {
            "allowed_tables": allowed,
            "source_count": len(sources),
            "note": "Read-only proxy: allow-list only; pass `sql` to execute a "
            "validated query.",
        }

    # Resolve which source to run against.
    if source_id is not None:
        chosen = next((s for s in sources if s.id == source_id), None)
        if chosen is None:
            raise DbProxyError(
                f"data source {source_id} not found for this application"
            )
    elif len(sources) == 1:
        chosen = sources[0]
    elif len(sources) == 0:
        raise DbProxyError("no data sources configured for this application")
    else:
        raise DbProxyError(
            "multiple data sources configured; pass source_id to disambiguate"
        )

    res = await execute_query(
        chosen.conn_secret_ref,
        chosen.allowed_tables or [],
        sql,
        connector=connector,
        mask=desensitize,
    )
    res["source_id"] = chosen.id
    res["source_name"] = chosen.name
    return res


async def get_memory(session, application_id: int, trigger_signature: str) -> dict[str, Any]:
    """Find a previously stored, still-valid conclusion for this signature."""
    result = await session.execute(
        select(Memory)
        .where(Memory.application_id == application_id)
        .where(Memory.trigger_signature == trigger_signature)
        .where(Memory.is_valid.is_(True))
        .order_by(Memory.updated_at.desc())
    )
    memory = result.scalars().first()
    if memory is None:
        return {"matched": False, "content": None, "memory_id": None}
    return {"matched": True, "content": memory.content, "memory_id": memory.id}


async def load_alert(session, alert_id: int | None) -> Alert | None:
    if alert_id is None:
        return None
    result = await session.execute(select(Alert).where(Alert.id == alert_id))
    return result.scalars().first()
