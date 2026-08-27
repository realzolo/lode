"""System-administrator user lifecycle endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from lode.api.audit import audit_action
from lode.api.deps import require_admin
from lode.api.types import EntityId
from lode.api.schemas import PasswordResetIn, UserCreateIn, UserOut, UserUpdateIn
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def _out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        is_system_admin=user.is_system_admin,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


@router.get("", response_model=list[UserOut])
async def list_users(_: int = Depends(require_admin)) -> list[UserOut]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    return [_out(row) for row in rows]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(payload: UserCreateIn, admin_id: EntityId = Depends(require_admin)) -> UserOut:
    username = payload.username.strip().lower()
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is not None:
            raise HTTPException(status_code=409, detail="username already registered")
        user = User(
            username=username,
            display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.initial_password),
            status="active",
            must_change_password=True,
            is_system_admin=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    await audit_action(
        action="user.create", actor_id=admin_id, target_type="user", target_id=str(user.id)
    )
    return _out(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: EntityId, payload: UserUpdateIn, admin_id: EntityId = Depends(require_admin)
) -> UserOut:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if user.is_system_admin:
            raise HTTPException(status_code=409, detail="system administrator is immutable")
        if payload.display_name is not None:
            user.display_name = payload.display_name.strip()
        if payload.status is not None:
            user.status = payload.status
        await session.commit()
        await session.refresh(user)
    await audit_action(
        action="user.update", actor_id=admin_id, target_type="user", target_id=str(user_id)
    )
    return _out(user)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: EntityId, payload: PasswordResetIn, admin_id: EntityId = Depends(require_admin)
) -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if user.is_system_admin:
            raise HTTPException(status_code=409, detail="system administrator resets their own password")
        user.password_hash = hash_password(payload.password)
        user.must_change_password = True
        await session.commit()
    await audit_action(
        action="user.reset_password", actor_id=admin_id, target_type="user", target_id=str(user_id)
    )
    return {"status": "ok", "message": "password reset"}
