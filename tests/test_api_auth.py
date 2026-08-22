"""Integration tests for the authentication boundary.

Uses an async ``httpx`` client pointed at the ASGI app (no lifespan, so Alembic
does not run during tests — the schema is already current). A throwaway test
user is created in the same event loop the requests run in, so the async engine
never crosses loop boundaries. Both the test user and any memory it could touch
are cleaned up afterwards.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from lode.api.main import app
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

TEST_EMAIL = f"test-auth-{uuid.uuid4().hex}@lode.local"
TEST_PASSWORD = "test-pass-123"


@pytest_asyncio.fixture
async def test_user() -> tuple[str, str, int]:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(User).where(User.email == TEST_EMAIL))
        ).scalars().first()
        if existing is not None:
            await session.delete(existing)
            await session.commit()
        user = User(email=TEST_EMAIL, name="Auth Test", role="user", status="active")
        user.password_hash = hash_password(TEST_PASSWORD)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        uid = user.id
    yield TEST_EMAIL, TEST_PASSWORD, uid
    async with AsyncSessionLocal() as session:
        victim = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if victim is not None:
            await session.delete(victim)
            await session.commit()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_login_rejects_wrong_password(test_user):
    email, _password, _uid = test_user
    async with _client() as client:
        resp = await client.post(
            "/auth/login", json={"email": email, "password": "not-the-right-one"}
        )
        assert resp.status_code == 401
        assert "error" in resp.json()


async def test_protected_route_requires_token(test_user):
    async with _client() as client:
        resp = await client.get("/analyses")
        assert resp.status_code == 401


async def test_login_and_token_grants_access(test_user):
    email, password, _uid = test_user
    async with _client() as client:
        resp = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body and body["token"]
        token = body["token"]

        # Protected route works with the token.
        resp2 = await client.get("/analyses", headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200

        # /auth/me reflects the authenticated principal.
        resp3 = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp3.status_code == 200
        assert resp3.json()["email"] == email

        # A malformed token is rejected.
        resp4 = await client.get(
            "/analyses", headers={"Authorization": "Bearer garbage.signature.token"}
        )
        assert resp4.status_code == 401

        # Missing scheme is rejected.
        resp5 = await client.get("/analyses", headers={"Authorization": token})
        assert resp5.status_code == 401


async def test_settings_requires_token_but_serves_masked_config(test_user):
    email, password, _uid = test_user
    async with _client() as client:
        unauthorized = await client.get("/settings")
        assert unauthorized.status_code == 401

        login = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        token = login.json()["token"]
        ok = await client.get("/settings", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        data = ok.json()
        # Secrets are masked, not echoed back.
        for model in data.get("ai_model_configs", []):
            assert "api_key" not in model
            assert "api_key_ref" not in model
            # Presence is signalled without leaking the value.
            assert "has_key" in model
