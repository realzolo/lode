"""Invitations (admin create/list, open accept).

Admins generate an invite for a prospective teammate's email. The invite
carries a single-use token. The recipient hits ``POST /invites/accept`` with
that token plus a password to activate their account — no existing session is
required, which is why the accept endpoint is intentionally open.

The login page links here so new users can finish onboarding via their invite
link.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException

from lode.api.deps import require_admin
from lode.api.schemas import (
    InviteAcceptIn,
    InviteCreateIn,
    InviteOut,
)
from lode.db.models.user import Invite, User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password
from sqlalchemy import select

router = APIRouter(prefix="/invites", tags=["invites"])


@router.post("", response_model=InviteOut, status_code=201)
async def create_invite(
    payload: InviteCreateIn, admin_id: int = Depends(require_admin)
) -> InviteOut:
    async with AsyncSessionLocal() as session:
        # Don't invite an email that already has an active account.
        existing_user = (
            await session.execute(select(User).where(User.email == payload.email))
        ).scalars().first()
        if existing_user is not None:
            raise HTTPException(status_code=409, detail="email already registered")

        token = secrets.token_urlsafe(32)
        invite = Invite(email=payload.email, token=token, invited_by=admin_id, status="pending")
        session.add(invite)
        await session.commit()
        await session.refresh(invite)
        return InviteOut(
            id=invite.id,
            email=invite.email,
            token=invite.token,
            status=invite.status,
            created_at=invite.created_at,
        )


@router.get("", response_model=list[InviteOut])
async def list_invites(_admin: int = Depends(require_admin)) -> list[InviteOut]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(select(Invite).order_by(Invite.created_at))
        ).scalars().all()
    return [
        InviteOut(id=i.id, email=i.email, token=i.token, status=i.status, created_at=i.created_at)
        for i in rows
    ]


@router.post("/accept")
async def accept_invite(payload: InviteAcceptIn) -> dict[str, str]:
    """Activate an account from an invite token. Open to unauthenticated callers."""
    async with AsyncSessionLocal() as session:
        invite = (
            await session.execute(select(Invite).where(Invite.token == payload.token))
        ).scalars().first()
        if invite is None:
            raise HTTPException(status_code=404, detail="invite not found")
        if invite.status != "pending":
            raise HTTPException(status_code=409, detail=f"invite is {invite.status}")

        # Double-check no account was created in the meantime.
        taken = (
            await session.execute(select(User).where(User.email == invite.email))
        ).scalars().first()
        if taken is not None:
            raise HTTPException(status_code=409, detail="email already registered")

        user = User(
            email=invite.email,
            name=payload.name,
            role="user",
            status="active",
            password_hash=hash_password(payload.password),
        )
        session.add(user)
        invite.status = "accepted"
        await session.commit()
        return {"status": "ok", "message": "account activated", "email": invite.email}
