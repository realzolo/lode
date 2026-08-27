"""Authentication routes: login (issue token) and current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import require_user
from lode.api.types import EntityId
from lode.api.audit import audit_action
from lode.api.schemas import (
    AuthLoginIn,
    PasswordChangeIn,
    TokenOut,
    UserOut,
)
from lode.config import settings
from lode.runtime_defaults import AUTH_TOKEN_TTL_SECONDS
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/login", response_model=TokenOut)
async def login(payload: AuthLoginIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    username = payload.username.strip().lower()
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    # Constant-ish failure: never reveal whether the username exists.
    if user is None or user.status != "active":
        await audit_action(
            action="auth.login",
            actor_username=username,
            target_type="user",
            result="failed",
            detail={"reason": "no_such_active_account"},
        )
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        await audit_action(
            action="auth.login",
            actor_id=user.id,
            target_type="user",
            target_id=str(user.id),
            result="failed",
            detail={"reason": "bad_password"},
        )
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = create_token(user.id, settings.jwt_signing_key, AUTH_TOKEN_TTL_SECONDS)
    await audit_action(
        action="auth.login",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        result="ok",
    )
    return TokenOut(
        token=token,
        user=UserOut(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            is_system_admin=user.is_system_admin,
            must_change_password=user.must_change_password,
            created_at=user.created_at,
        ),
    )


@router.get("/me", response_model=UserOut)
async def me(user_id: EntityId = Depends(require_user)) -> UserOut:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="user not found")
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        is_system_admin=user.is_system_admin,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


@router.post("/change-password")
async def change_password(
    payload: PasswordChangeIn,
    user_id: EntityId = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Let the authenticated user change their own password."""
    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="user not found")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    await session.commit()
    await audit_action(
        action="auth.change_password",
        actor_id=user_id,
        target_type="user",
        target_id=str(user_id),
    )
    return {"status": "ok", "message": "password updated"}
