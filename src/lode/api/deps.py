"""Auth dependencies.

``require_user`` validates the ``Authorization: Bearer <token>`` header and
returns the authenticated user id. The token is HMAC-signed (see
``lode.security``), so a valid decode proves authenticity; routes
that need the full user object load it from the database themselves.

Workspace authorization lives here too. ``WorkspacePermission`` rows grant an
ordinary Workbench user ``viewer`` or ``operator`` access to one Workspace.
The system administrator is unrestricted and can access both portals.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from fastapi.security import SecurityScopes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.config import settings
from lode.api.types import EntityId
from lode.db.models import User, WorkspacePermission
from lode.db.session import AsyncSessionLocal
from lode.security import decode_token

# Permission hierarchy: a higher rank satisfies any lower requirement.
PERM_RANK = {"viewer": 1, "operator": 2}


def _rank(perm: str) -> int:
    return PERM_RANK.get(perm, 0)


def require_user(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token, settings.jwt_signing_key)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")
    sub = claims.get("sub")
    if not isinstance(sub, int):
        raise HTTPException(status_code=401, detail="invalid token subject")
    return sub


async def require_admin(user_id: EntityId = Depends(require_user)) -> int:
    """Require the active, password-ready system administrator."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if user is None or not user.is_system_admin:
        raise HTTPException(status_code=403, detail="admin_access_forbidden")
    if user.status != "active":
        raise HTTPException(status_code=401, detail="active_user_required")
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="password_change_required")
    return user_id


async def require_workbench_user(user_id: EntityId = Depends(require_user)) -> int:
    """Require an active user whose initial password was changed."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="active_user_required")
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="password_change_required")
    return user_id


async def assert_workspace_permission(
    session: AsyncSession,
    user: User,
    workspace_id: EntityId,
    required_perm: str,
) -> None:
    """Raise unless ``user`` has the required Workspace permission."""
    if user.is_system_admin:
        return
    row = await session.get(WorkspacePermission, (user.id, workspace_id))
    if row is None or _rank(row.permission) < _rank(required_perm):
        raise HTTPException(
            status_code=403,
            detail="insufficient Workspace permission",
        )


async def require_workspace_permission(
    security_scopes: SecurityScopes,
    workspace_id: EntityId,
    user_id: EntityId = Depends(require_user),
) -> int:
    """FastAPI dependency enforcing a Workspace permission level."""
    required = security_scopes.scopes[0] if security_scopes.scopes else "read"
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="user not found")
        await assert_workspace_permission(session, user, workspace_id, required)
    return user_id


async def permitted_workspace_ids(
    session: AsyncSession, user_id: EntityId, is_system_admin: bool = False
) -> set[int] | None:
    """Workspace ids the user may read, or ``None`` when unrestricted."""
    if is_system_admin:
        return None
    rows = (
        (
            await session.execute(
                select(WorkspacePermission.workspace_id).where(
                    WorkspacePermission.user_id == user_id
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)
