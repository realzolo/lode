"""Controlled tools available to the analysis agent.

Every tool here is *read-only* and pulls its context from the platform's own
database (repository registry, preset prompts, table whitelist, shared
memory). They are the safe, auditable surface the agent is allowed to use —
no arbitrary shell, no write access to production data.

``run_readonly_query`` proxies validated, read-only SQL to an application's
configured replica (see :mod:`lode.engine.db_proxy`): it enforces the
allow-list, rejects writes, resolves the connection (structured fields or a
secret ref), and masks sensitive columns before returning rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from lode.config import settings
from lode.crypto import decrypt_secret
from lode.db.models.alert import Alert
from lode.db.models.application import (
    ApplicationRepo,
    DbSource,
    PresetPrompt,
)
from lode.db.models.git import GitRepo
from lode.db.models.intake import EvidenceArtifact
from lode.db.models.memory import Memory
from lode.engine.db_proxy import DbConnector, DbProxyError, execute_query


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
    analysis_id: int | None = None,
) -> dict[str, Any]:
    """Read-only proxy for an application's whitelisted data sources.

    With no ``sql`` this returns the allow-list (used to brief the analysis
    agent about what it may read). When ``sql`` is provided the statement is
    validated against the chosen source's ``allowed_tables`` and executed
    read-only against the resolved replica, with sensitive columns **always**
    masked.

    When ``analysis_id`` is supplied the (already-masked) result set is persisted
    as an ``EvidenceArtifact`` (type ``db_query``) so the query becomes citable,
    auditable evidence for that analysis run.
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

    # The stored password is encrypted at rest; decrypt it for the connect call.
    # A decrypt failure (e.g. after a secret_key rotation) is surfaced as a
    # resolvable-source error rather than a 500.
    try:
        source_password = decrypt_secret(chosen.password)
    except Exception as exc:
        raise DbProxyError(f"data source credentials are unreadable: {exc}") from exc

    res = await execute_query(
        chosen.conn_secret_ref,
        chosen.allowed_tables or [],
        sql,
        host=chosen.host,
        port=chosen.port,
        database=chosen.database,
        username=chosen.username,
        password=source_password,
        sslmode=chosen.sslmode,
        sensitive_columns=chosen.sensitive_columns or [],
        connector=connector,
    )
    res["source_id"] = chosen.id
    res["source_name"] = chosen.name

    if analysis_id is not None:
        artifact_id = await persist_db_query_artifact(
            session,
            analysis_id=analysis_id,
            source_id=chosen.id,
            source_name=chosen.name,
            sql=sql,
            columns=res["columns"],
            rows=res["rows"],
            truncated=res["truncated"],
            referenced_tables=res["tables"],
        )
        res["evidence_artifact_id"] = artifact_id
    return res


# How much of a DB-query result set to store as an evidence excerpt, and how long
# the excerpt string / the originating SQL may be before truncation.
_DB_QUERY_EXCERPT_ROWS = 50
_DB_QUERY_EXCERPT_CHARS = 20_000
_DB_QUERY_SQL_CHARS = 10_000


async def persist_db_query_artifact(
    session,
    *,
    analysis_id: int,
    source_id: int,
    source_name: str,
    sql: str,
    columns: list[str],
    rows: list[dict],
    truncated: bool,
    referenced_tables: list[str],
) -> int:
    """Persist a DB-query result as an ``EvidenceArtifact`` (type ``db_query``).

    Rows are stored *already masked* (the proxy desensitizes before returning),
    so the artifact never holds raw PII. A SHA-256 ``content_hash`` lets the UI
    dedupe identical result sets, and ``retention_until`` honors the platform
    retention policy. Returns the new artifact id.
    """
    import hashlib
    import json

    excerpt_rows = rows[:_DB_QUERY_EXCERPT_ROWS]
    excerpt = json.dumps(
        {"columns": columns, "rows": excerpt_rows, "truncated": truncated},
        default=str,
    )
    digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()

    retention = None
    days = settings.evidence_retention_days
    if days and days > 0:
        retention = datetime.now(UTC) + timedelta(days=days)

    artifact = EvidenceArtifact(
        analysis_id=analysis_id,
        artifact_type="db_query",
        source_kind="db",
        source_id=source_id,
        locator=f"db://source/{source_id}?name={source_name}",
        content_hash=digest,
        redacted_excerpt=excerpt[:_DB_QUERY_EXCERPT_CHARS],
        metadata_={
            "statement": sql[:_DB_QUERY_SQL_CHARS],
            "columns": columns,
            "row_count": len(rows),
            "truncated": truncated,
            "tables": sorted(referenced_tables),
        },
        retention_until=retention,
    )
    session.add(artifact)
    await session.flush()
    return artifact.id


async def get_memory(
    session,
    application_id: int,
    *,
    query_text: str | None = None,
    dedupe_key: str | None = None,
    embed_fn=None,
    search_fn=None,
    top_k: int = 5,
    threshold: float = 0.25,
) -> dict[str, Any]:
    """Find a previously stored, still-valid conclusion for an incident.

    Resolution order:

    1. **Exact match** — if ``dedupe_key`` is supplied and a still-valid memory
       shares that trigger_signature, it wins. This preserves the original
       deterministic behaviour and guarantees re-analysis of the same incident
       returns its recorded conclusion.
    2. **Semantic match** — when both ``embed_fn`` and ``search_fn`` are wired
       (an embedding provider is configured), embed ``query_text`` and ask
       ``search_fn`` for the nearest memories by cosine distance; the closest
       candidate within ``threshold`` distance is returned. This is what lets
       a *new* incident reuse a conclusion from a semantically similar past
       incident even when the exact signature differs.
    3. **No match** — otherwise an explicit miss is returned.

    Returns a dict with ``matched``, ``content``, ``memory_id``, ``similarity``,
    ``match_type`` (``exact`` | ``semantic`` | ``none``) and ``candidates``
    (the ranked semantic neighbours, for transparency in the UI/evidence).
    """
    # 1. Exact signature override (deterministic + backward compatible).
    if dedupe_key:
        # Skip expired conclusions (T8): a stale memory must not shadow a fresh
        # analysis of the same incident.
        now_utc = datetime.now(UTC)
        exact = (
            await session.execute(
                select(Memory)
                .where(Memory.application_id == application_id)
                .where(Memory.trigger_signature == dedupe_key)
                .where(Memory.is_valid.is_(True))
                .where(or_(Memory.expires_at.is_(None), Memory.expires_at > now_utc))
                .order_by(Memory.updated_at.desc())
            )
        ).scalars().first()
        if exact is not None:
            return {
                "matched": True,
                "content": exact.content,
                "memory_id": exact.id,
                "similarity": 1.0,
                "match_type": "exact",
                "candidates": [],
            }

    # 2. Semantic nearest-neighbour search when embeddings are available.
    if embed_fn is not None and search_fn is not None and query_text:
        query_vec = await embed_fn(query_text)
        if query_vec:
            ranked = await search_fn(session, application_id, query_vec, top_k)
            candidates = _rank_to_candidates(ranked)
            best = next(
                (c for c in candidates if c["distance"] <= threshold), None
            )
            if best is not None:
                mem = next(m for m, _ in ranked if m.id == best["memory_id"])
                return {
                    "matched": True,
                    "content": mem.content,
                    "memory_id": mem.id,
                    "similarity": best["similarity"],
                    "match_type": "semantic",
                    "candidates": candidates,
                }
            return {
                "matched": False,
                "content": None,
                "memory_id": None,
                "similarity": None,
                "match_type": "semantic",
                "candidates": candidates,
            }

    # 3. Fallback: no embeddings configured, no exact hit.
    return {
        "matched": False,
        "content": None,
        "memory_id": None,
        "similarity": None,
        "match_type": "none",
        "candidates": [],
    }


def _rank_to_candidates(ranked) -> list[dict]:
    return [
        {
            "memory_id": mem.id,
            "distance": round(float(dist), 4),
            "similarity": round(max(0.0, 1.0 - float(dist)), 4),
        }
        for mem, dist in ranked
    ]


async def load_alert(session, alert_id: int | None) -> Alert | None:
    if alert_id is None:
        return None
    result = await session.execute(select(Alert).where(Alert.id == alert_id))
    return result.scalars().first()
