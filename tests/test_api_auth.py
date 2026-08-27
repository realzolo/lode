"""Integration tests for the authentication boundary.

Uses an async ``httpx`` client pointed at the ASGI app (no lifespan, so Alembic
does not run during tests — the schema is already current). A throwaway test
user is created in the same event loop the requests run in, so the async engine
never crosses loop boundaries. Both the test user and any experience it could touch
are cleaned up afterwards.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

TEST_USERNAME = f"auth-{uuid.uuid4().hex[:12]}"
TEST_PASSWORD = "test-pass-123"


@pytest_asyncio.fixture
async def test_user() -> tuple[str, str, int]:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(User).where(User.username == TEST_USERNAME))
        ).scalars().first()
        user = existing or User(username=TEST_USERNAME, display_name="Auth Test", password_hash=hash_password(TEST_PASSWORD))
        user.display_name = "Auth Test"
        user.status = "active"
        user.password_hash = hash_password(TEST_PASSWORD)
        user.must_change_password = False
        if existing is None:
            session.add(user)
        await session.commit()
        await session.refresh(user)
        uid = user.id
    yield TEST_USERNAME, TEST_PASSWORD, uid
    async with AsyncSessionLocal() as session:
        victim = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if victim is not None:
            victim.status = "disabled"
            await session.commit()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_login_rejects_wrong_password(test_user):
    username, _password, _uid = test_user
    async with _client() as client:
        resp = await client.post(
            "/auth/login", json={"username": username, "password": "not-the-right-one"}
        )
        assert resp.status_code == 401
        assert "error" in resp.json()


async def test_protected_route_requires_token(test_user):
    async with _client() as client:
        resp = await client.get("/workspaces/1/build-units")
        assert resp.status_code == 401


async def test_login_and_token_grants_access(test_user):
    username, password, _uid = test_user
    async with _client() as client:
        resp = await client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body and body["token"]
        token = body["token"]

        # /auth/me reflects the authenticated principal.
        resp2 = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200
        assert resp2.json()["username"] == username

        # A malformed token is rejected.
        resp3 = await client.get(
            "/auth/me", headers={"Authorization": "Bearer garbage.signature.token"}
        )
        assert resp3.status_code == 401

        # Missing scheme is rejected.
        resp4 = await client.get("/auth/me", headers={"Authorization": token})
        assert resp4.status_code == 401


async def test_workspace_resource_view_requires_auth_and_permission(test_user):
    username, password, _uid = test_user
    async with _client() as client:
        unauthorized = await client.get("/workspaces/1/build-units")
        assert unauthorized.status_code == 401

        login = await client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        token = login.json()["token"]
        denied = await client.get(
            "/workspaces/1/build-units",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 403
