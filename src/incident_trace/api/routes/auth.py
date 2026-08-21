"""Authentication routes: login (issue token) and current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from incident_trace.api.deps import require_user
from incident_trace.api.schemas import AuthLoginIn, TokenOut, UserOut
from incident_trace.config import settings
from incident_trace.db.models.user import User
from incident_trace.db.session import AsyncSessionLocal
from incident_trace.security import create_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.post("/login", response_model=TokenOut)
async def login(payload: AuthLoginIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalars().first()
    # Constant-ish failure: never reveal whether the email exists.
    if user is None or user.password_hash is None or user.status != "active":
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = create_token(user.id, settings.secret_key, settings.jwt_ttl_seconds)
    return TokenOut(
        token=token,
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "status": user.status,
        },
    )


@router.get("/me", response_model=UserOut)
async def me(user_id: int = Depends(require_user)) -> UserOut:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role, status=user.status)
