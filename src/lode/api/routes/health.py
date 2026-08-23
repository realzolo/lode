"""Health check routes.

Two probes, following the Kubernetes liveness/readiness model:

  * ``GET /health/live``  — liveness. The process is up; Kubernetes restarts
    the pod when this stops answering. It never touches dependencies, so a
    slow database cannot cause a crash-loop.
  * ``GET /health`` (alias ``/health/ready``) — readiness. The app can serve
    real traffic *right now*. It pings the database; if the DB is unreachable
    it returns 503 so the load balancer stops routing to this instance until
    it recovers.

A 200 on ``/health`` does NOT mean "healthy forever" — it means "ready now".
Operators should point their liveness probe at ``/health/live`` and their
readiness probe at ``/health``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from lode.db.session import AsyncSessionLocal

router = APIRouter(tags=["health"])

logger = logging.getLogger("lode.api.health")


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Liveness probe — process is alive, no dependency checks."""
    return {"status": "ok"}


async def _database_reachable() -> bool:
    """Cheap ``SELECT 1`` against the primary. Returns False on any error."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("readiness probe failed: database unreachable")
        return False


@router.get("/health")
@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, str]:
    """Readiness probe — depends on the database being reachable."""
    if not await _database_reachable():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "detail": "database unreachable"}
    return {"status": "ok"}
