"""Auth dependencies.

``require_user`` validates the ``Authorization: Bearer <token>`` header and
returns the authenticated user id. The token is HMAC-signed (see
``lode.security``), so a valid decode proves authenticity; routes
that need the full user object load it from the database themselves.

Per-application authorization lives here too. ``UserApplicationPerm`` rows
grant a user a role (``read`` / ``analyze`` / ``admin``) on a single
application. ``require_app_perm`` enforces that a caller may act on a given
application at the required level — global admins always pass.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import SecurityScopes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models.permission import UserApplicationPerm
from lode.db.models.user import User
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


async def assert_app_perm(
    session: AsyncSession,
    user: User,
    application_id: int,
    required_perm: str,
) -> None:
    """Raise 403 unless ``user`` may act on ``application_id`` at ``required_perm``.

    Global admins bypass all per-app checks. Other users need a
    ``UserApplicationPerm`` row whose rank meets the requirement.
    """
    if user.role == "admin":
        return
    row = await session.get(UserApplicationPerm, (user.id, application_id))
    if row is None or _rank(row.perm) < _rank(required_perm):
        raise HTTPException(
            status_code=403,
            detail="insufficient application permission",
        )


async def require_app_perm(
    security_scopes: SecurityScopes,
    application_id: int,
    user_id: int = Depends(require_user),
) -> int:
    """FastAPI ``Security`` dependency: enforce a per-app permission level.

    Usage::

        @router.get("/{application_id}/members")
        async def ...(
            application_id: int,
            _auth: int = Security(require_app_perm, scopes=["admin"]),
        ):
    """
    required = security_scopes.scopes[0] if security_scopes.scopes else "read"
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="user not found")
        await assert_app_perm(session, user, application_id, required)
    return user_id


async def permitted_app_ids(
    session: AsyncSession, user_id: int, role: str
) -> set[int] | None:
    """Application ids ``user`` may read, or ``None`` when unrestricted.

    Returns ``None`` for global admins (they see every application); a set of
    application ids otherwise. An empty set means "no access".
    """
    if role == "admin":
        return None
    rows = (
        await session.execute(
            select(UserApplicationPerm.application_id).where(
                UserApplicationPerm.user_id == user_id
            )
        )
    ).scalars().all()
    return set(rows)
