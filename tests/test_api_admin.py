"""Integration tests for admin user/AI-model management and invites.

Mirrors ``test_api_auth.py``: an async ``httpx`` client drives the ASGI app with
no lifespan (schema already current). Admin and regular users are created in
the same loop the requests run in. Everything created is torn down afterwards so
repeated runs stay idempotent.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from incident_trace.api.main import app
from incident_trace.db.models.ai_model import AiModelConfig
from incident_trace.db.models.user import Invite, User
from incident_trace.db.session import AsyncSessionLocal
from incident_trace.security import hash_password

ADMIN_EMAIL = f"admin-{uuid.uuid4().hex}@incident-trace.local"
ADMIN_PASSWORD = "admin-pass-1"
USER_EMAIL = f"user-{uuid.uuid4().hex}@incident-trace.local"
USER_PASSWORD = "user-pass-1"


async def _make_user(email: str, password: str, role: str) -> int:
    async with AsyncSessionLocal() as session:
        u = User(email=email, name=role, role=role, status="active")
        u.password_hash = hash_password(password)
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u.id


@pytest_asyncio.fixture
async def admin() -> tuple[str, str, int]:
    uid = await _make_user(ADMIN_EMAIL, ADMIN_PASSWORD, "admin")
    yield ADMIN_EMAIL, ADMIN_PASSWORD, uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


@pytest_asyncio.fixture
async def user() -> tuple[str, str, int]:
    uid = await _make_user(USER_EMAIL, USER_PASSWORD, "user")
    yield USER_EMAIL, USER_PASSWORD, uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


async def _login(email: str, password: str) -> str:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- AI model config (admin) --------------------------------------------

async def test_non_admin_cannot_create_ai_model(user):
    _email, _pw, _uid = user
    token = await _login(USER_EMAIL, USER_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            "/settings/ai-models",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "scope": "global",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key_ref": "env://OPENAI_API_KEY",
                "model": "gpt-4o-mini",
                "is_default": True,
            },
        )
        assert resp.status_code == 403


async def test_admin_crud_ai_model(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        # create
        resp = await client.post(
            "/settings/ai-models",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "scope": "global",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key_ref": "env://OPENAI_API_KEY",
                "model": "gpt-4o-mini",
                "is_default": True,
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["is_default"] is True
        assert created["has_key"] is True
        mid = created["id"]

        # update (metadata only, key omitted -> preserved)
        resp = await client.put(
            f"/settings/ai-models/{mid}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "scope": "global",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key_ref": "",
                "model": "gpt-4o",
                "is_default": True,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["model"] == "gpt-4o"
        assert resp.json()["has_key"] is True

        # delete
        resp = await client.delete(
            f"/settings/ai-models/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204


# --- User management (admin) --------------------------------------------

async def test_admin_user_crud(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        email = f"created-{uuid.uuid4().hex}@incident-trace.local"
        resp = await client.post(
            "/users",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": email, "name": "New", "role": "user", "password": "new-pass-1"},
        )
        assert resp.status_code == 201, resp.text
        new_id = resp.json()["id"]

        # the new user can log in
        login = await client.post("/auth/login", json={"email": email, "password": "new-pass-1"})
        assert login.status_code == 200

        # admin resets password
        resp = await client.post(
            f"/users/{new_id}/reset-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "reset-pass-1"},
        )
        assert resp.status_code == 200

        # cleanup
        await client.delete(f"/users/{new_id}", headers={"Authorization": f"Bearer {token}"})


async def test_admin_cannot_delete_self(admin):
    _email, _pw, uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        resp = await client.delete(f"/users/{uid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409


# --- Self-service password change ---------------------------------------

async def test_change_own_password(user):
    _email, _pw, _uid = user
    token = await _login(USER_EMAIL, USER_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            "/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": USER_PASSWORD, "new_password": "changed-pass-1"},
        )
        assert resp.status_code == 200, resp.text
        # new password works
        login = await client.post(
            "/auth/login", json={"email": USER_EMAIL, "password": "changed-pass-1"}
        )
        assert login.status_code == 200


# --- Invites -------------------------------------------------------------

async def test_invite_create_and_accept(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    email = f"invited-{uuid.uuid4().hex}@incident-trace.local"
    async with _client() as client:
        resp = await client.post(
            "/invites",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": email},
        )
        assert resp.status_code == 201, resp.text
        token_str = resp.json()["token"]

        # accept (open endpoint, no auth)
        resp = await client.post(
            "/invites/accept",
            json={"token": token_str, "password": "invitee-pass-1", "name": "Invitee"},
        )
        assert resp.status_code == 200, resp.text

        # the accepted account can now log in
        login = await client.post("/auth/login", json={"email": email, "password": "invitee-pass-1"})
        assert login.status_code == 200

        # cleanup the accepted user
        async with AsyncSessionLocal() as session:
            v = (await session.execute(select(User).where(User.email == email))).scalars().first()
            if v is not None:
                await session.delete(v)
                await session.commit()
            inv = (await session.execute(select(Invite).where(Invite.token == token_str))).scalars().first()
            if inv is not None:
                await session.delete(inv)
                await session.commit()
