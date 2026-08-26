"""Auth dependencies.

``require_user`` validates the ``Authorization: Bearer <token>`` header and
returns the authenticated user id. The token is HMAC-signed (see
``lode.security``), so a valid decode proves authenticity; routes
that need the full user object load it from the database themselves.

Workspace authorization lives here too. ``WorkspacePermission`` rows grant a
user ``read`` / ``analyze`` / ``admin`` access to one Workspace. Global admins
always pass.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from fastapi.security import SecurityScopes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models import User, WorkspacePermission
from lode.db.session import AsyncSessionLocal
from lode.security import decode_token
from lode.config import settings

# Permission hierarchy: a higher rank satisfies any lower requirement.
PERM_RANK = {"read": 1, "analyze": 2, "admin": 3}


def _rank(perm: str) -> int:
    return PERM_RANK.get(perm, 0)


def require_user(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token, settings.secret_key)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")
    sub = claims.get("sub")
    if not isinstance(sub, int):
        raise HTTPException(status_code=401, detail="invalid token subject")
    return sub


async def require_admin(user_id: int = Depends(require_user)) -> int:
    """Require an authenticated *admin*; returns the admin user id."""
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if user is None or user.role != "admin":
        raise HTTPException(status_code=403, detail="admin privileges required")
    return user_id


async def assert_workspace_permission(
    session: AsyncSession,
    user: User,
    workspace_id: int,
    required_perm: str,
) -> None:
    """Raise unless ``user`` has the required Workspace permission."""
    if user.role == "admin":
        return
    row = await session.get(WorkspacePermission, (workspace_id, user.id))
    if row is None or _rank(row.permission) < _rank(required_perm):
        raise HTTPException(
            status_code=403,
            detail="insufficient Workspace permission",
        )


async def require_workspace_permission(
    security_scopes: SecurityScopes,
    workspace_id: int,
    user_id: int = Depends(require_user),
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
    session: AsyncSession, user_id: int, role: str
) -> set[int] | None:
    """Workspace ids the user may read, or ``None`` when unrestricted."""
    if role == "admin":
        return None
    rows = (
        await session.execute(
            select(WorkspacePermission.workspace_id).where(
                WorkspacePermission.user_id == user_id
            )
        )
    ).scalars().all()
    return set(rows)
