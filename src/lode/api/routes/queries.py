"""Approved read-only query catalog for the developer workbench.

Operators select a reviewed operation and an administrator-approved base table;
there is intentionally no SQL input anywhere on this route.

The endpoint requires the ``analyze`` application permission so only readers
with investigation rights (and global admins) can touch the live replica — a
bare ``read`` member may view analysis results but not fire queries.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.audit import audit_action
from lode.api.deps import require_app_perm
from lode.api.schemas import RunApprovedQueryIn
from lode.db.session import AsyncSessionLocal
from lode.engine.db_proxy import DbProxyError
from lode.engine.tools import run_approved_query

router = APIRouter(prefix="/applications", tags=["query"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/{application_id}/query")
async def run_query(
    application_id: int,
    payload: RunApprovedQueryIn,
    _auth: int = Security(require_app_perm, scopes=["analyze"]),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Execute one reviewed read-only template against an approved base table."""
    try:
        res = await run_approved_query(
            session,
            application_id,
            source_id=payload.source_id, table=payload.table, operation=payload.operation,
        )
        await audit_action(
            action="query.execute",
            actor_id=_auth,
            target_type="application",
            target_id=str(application_id),
            application_id=application_id,
            result="ok",
            detail={"source_id": res.get("source_id"), "tables": res.get("tables"), "operation": payload.operation},
        )
        return res
    except DbProxyError as exc:
        await audit_action(
            action="query.execute",
            actor_id=_auth,
            target_type="application",
            target_id=str(application_id),
            application_id=application_id,
            result="error",
            detail={"error": str(exc)},
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
