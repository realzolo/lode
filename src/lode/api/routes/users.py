"""User administration (admin only).

Operators with the ``admin`` role can list, create, update, and delete users,
and reset any user's password. Deleting or disabling the *last* admin is
blocked to avoid locking everyone out of the console.

Self-service password changes live on ``/auth/change-password`` (see
``routes/auth.py``) and do not require admin rights.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from lode.api.deps import require_admin, require_user
from lode.api.audit import audit_action
from lode.api.schemas import(
    PasswordResetIn,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password, verify_password
from sqlalchemy import select

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(_admin: int = Depends(require_admin)) -> list[UserOut]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(select(User).order_by(User.created_at))
        ).scalars().all()
    return [
        UserOut(
            id=u.id,
            email=u.email,
            name=u.name,
            role=u.role,
            status=u.status,
            created_at=u.created_at,
        )
        for u in rows
    ]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreateIn, _admin: int = Depends(require_admin)
) -> UserOut:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(User).where(User.email == payload.email))
        ).scalars().first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="email already registered")

        user = User(
            email=payload.email,
            name=payload.name,
            role=payload.role,
            status="active",
            password_hash=hash_password(payload.password),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        await audit_action(
            action="user.create",
            actor_id=_admin,
            target_type="user",
            target_id=str(user.id),
        )
        return UserOut(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status,
            created_at=user.created_at,
        )


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int, payload: UserUpdateIn, _admin: int = Depends(require_admin)
) -> UserOut:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")

        # Guard: never leave the platform without an active admin.
        if user.role == "admin" and payload.role == "user":
            admin_count = (
                await session.execute(
                    select(User.id).where(User.role == "admin").where(User.status == "active")
                )
            ).scalars().all()
            if len(admin_count) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="cannot demote the last active admin",
                )

        if payload.name is not None:
            user.name = payload.name
        if payload.role is not None:
            user.role = payload.role
        if payload.status is not None:
            user.status = payload.status
        await session.commit()
        await session.refresh(user)
        await audit_action(
            action="user.update",
            actor_id=_admin,
            target_type="user",
            target_id=str(user_id),
        )
        return UserOut(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status,
            created_at=user.created_at,
        )


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int, payload: PasswordResetIn, _admin: int = Depends(require_admin)
) -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        user.password_hash = hash_password(payload.password)
        await session.commit()
        await audit_action(
            action="user.reset_password",
            actor_id=_admin,
            target_type="user",
            target_id=str(user_id),
        )
        return {"status": "ok", "message": "password reset"}


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: int, admin_id: int = Depends(require_admin)) -> None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        if user.id == admin_id:
            raise HTTPException(status_code=409, detail="cannot delete yourself")
        if user.role == "admin":
            admin_count = (
                await session.execute(
                    select(User.id).where(User.role == "admin").where(User.status == "active")
                )
            ).scalars().all()
            if len(admin_count) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="cannot delete the last active admin",
                )
        await session.delete(user)
        await session.commit()
        await audit_action(
            action="user.delete",
            actor_id=admin_id,
            target_type="user",
            target_id=str(user_id),
        )
