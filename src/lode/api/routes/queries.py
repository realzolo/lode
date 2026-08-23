"""Ad-hoc read-only query console for the developer workbench.

Lets an analyst run a *validated* SQL query against one of the application's
whitelisted, read-only replicas. The heavy lifting (read-only enforcement, table
allow-list, DSN resolution, desensitization) lives in
``lode.engine.db_proxy``; this router is the thin, permission-gated HTTP surface.

The endpoint requires the ``analyze`` application permission so only readers
with investigation rights (and global admins) can touch the live replica — a
bare ``read`` member may view analysis results but not fire queries.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Security
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import require_app_perm
from lode.api.schemas import RunQueryIn
from lode.db.session import AsyncSessionLocal
from lode.engine.db_proxy import DbProxyError
from lode.engine.tools import run_readonly_query

router = APIRouter(prefix="/applications", tags=["query"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/{application_id}/query")
async def run_query(
    application_id: int,
    payload: RunQueryIn,
    _auth: int = Security(require_app_perm, scopes=["analyze"]),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Execute a validated, read-only query against the application's replica.

    Rejects writes, queries that touch non-allow-listed tables, and any source
    that cannot be resolved. Masked sensitive columns are returned as ``***``
    unless ``desensitize`` is explicitly set to false.
    """
    try:
        return await run_readonly_query(
            session,
            application_id,
            sql=payload.sql,
            source_id=payload.source_id,
            desensitize=payload.desensitize,
        )
    except DbProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
