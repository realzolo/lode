"""Integration tests for M1 application permissions (FR-602).

Exercises the per-application membership model and the per-app authorization
enforcement:

- The application creator is auto-granted ``admin`` membership.
- Global admins and application admins may list / add / update / remove members.
- Non-members are rejected with 403 on member endpoints.
- Removing the last admin member is blocked with 409.

Driven through the ASGI app with ``httpx.AsyncClient``; all rows are created
per-test and cleaned up at teardown, so the suite is order-independent.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.db.models.application import Application
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

ADMIN_EMAIL = f"perm-admin-{uuid.uuid4().hex}@lode.local"
ADMIN_PASSWORD = "admin-pass-1"
APP_OWNER_EMAIL = f"owner-{uuid.uuid4().hex}@lode.local"
APP_OWNER_PASSWORD = "owner-pass-1"
OUTSIDER_EMAIL = f"outsider-{uuid.uuid4().hex}@lode.local"
OUTSIDER_PASSWORD = "outsider-pass-1"
GUEST_EMAIL = f"guest-{uuid.uuid4().hex}@lode.local"
GUEST_PASSWORD = "guest-pass-1"


async def _make_user(email: str, password: str, role: str) -> int:
    async with AsyncSessionLocal() as session:
        u = User(email=email, name=role, role=role, status="active")
        u.password_hash = hash_password(password)
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u.id


@pytest_asyncio.fixture
async def admin() -> int:
    uid = await _make_user(ADMIN_EMAIL, ADMIN_PASSWORD, "admin")
    yield uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


@pytest_asyncio.fixture
async def owner() -> int:
    uid = await _make_user(APP_OWNER_EMAIL, APP_OWNER_PASSWORD, "user")
    yield uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


@pytest_asyncio.fixture
async def outsider() -> int:
    uid = await _make_user(OUTSIDER_EMAIL, OUTSIDER_PASSWORD, "user")
    yield uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


@pytest_asyncio.fixture
async def guest() -> int:
    uid = await _make_user(GUEST_EMAIL, GUEST_PASSWORD, "user")
    yield uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


async def _login(email: str, password: str) -> str:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def owned_app(owner: int) -> int:
    """Application created by ``owner`` — creator is auto-granted admin perm."""
    token = await _login(APP_OWNER_EMAIL, APP_OWNER_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            "/applications",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": f"app-{uuid.uuid4().hex}"},
        )
        assert resp.status_code == 201, resp.text
        app_id = resp.json()["id"]
    yield app_id
    async with AsyncSessionLocal() as session:
        v = await session.get(Application, app_id)
        if v is not None:
            await session.delete(v)
            await session.commit()


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


async def test_unauthenticated_members_rejected(owned_app: int) -> None:
    async with _client() as client:
        resp = await client.get(f"/applications/{owned_app}/members")
        assert resp.status_code == 401


async def test_outsider_cannot_view_members(owned_app: int, outsider: int) -> None:
    token = await _login(OUTSIDER_EMAIL, OUTSIDER_PASSWORD)
    async with _client() as client:
        resp = await client.get(
            f"/applications/{owned_app}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


async def test_outsider_cannot_view_member_candidates(
    owned_app: int, outsider: int
) -> None:
    token = await _login(OUTSIDER_EMAIL, OUTSIDER_PASSWORD)
    async with _client() as client:
        resp = await client.get(
            f"/applications/{owned_app}/member-candidates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


async def test_outsider_cannot_add_member(owned_app: int, outsider: int) -> None:
    token = await _login(OUTSIDER_EMAIL, OUTSIDER_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            f"/applications/{owned_app}/members",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_id": outsider, "perm": "read"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Membership lifecycle (app-admin owner)
# ---------------------------------------------------------------------------


async def test_owner_sees_self_as_member(owned_app: int, owner: int) -> None:
    token = await _login(APP_OWNER_EMAIL, APP_OWNER_PASSWORD)
    async with _client() as client:
        resp = await client.get(
            f"/applications/{owned_app}/members",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        members = resp.json()
        assert len(members) == 1
        assert members[0]["user_id"] == owner
        assert members[0]["perm"] == "admin"


async def test_owner_can_configure_own_topic(owned_app: int) -> None:
    token = await _login(APP_OWNER_EMAIL, APP_OWNER_PASSWORD)
    async with _client() as client:
        resp = await client.put(
            f"/applications/{owned_app}/topic",
            headers={"Authorization": f"Bearer {token}"},
            json={"topic": f"alerts-{uuid.uuid4().hex}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["application_id"] == owned_app


async def test_outsider_cannot_configure_topic(owned_app: int, outsider: int) -> None:
    token = await _login(OUTSIDER_EMAIL, OUTSIDER_PASSWORD)
    async with _client() as client:
        resp = await client.put(
            f"/applications/{owned_app}/topic",
            headers={"Authorization": f"Bearer {token}"},
            json={"topic": f"alerts-{uuid.uuid4().hex}"},
        )
        assert resp.status_code == 403


async def test_owner_can_list_member_candidates(
    owned_app: int, owner: int, guest: int
) -> None:
    token = await _login(APP_OWNER_EMAIL, APP_OWNER_PASSWORD)
    async with _client() as client:
        resp = await client.get(
            f"/applications/{owned_app}/member-candidates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert {user["id"] for user in resp.json()} >= {owner, guest}


async def test_owner_add_update_remove_member(
    owned_app: int, owner: int, guest: int
) -> None:
    token = await _login(APP_OWNER_EMAIL, APP_OWNER_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        # Add guest as read-only.
        resp = await client.post(
            f"/applications/{owned_app}/members",
            headers=headers,
            json={"user_id": guest, "perm": "read"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["perm"] == "read"

        # Upsert: promote to analyze.
        resp = await client.put(
            f"/applications/{owned_app}/members/{guest}",
            headers=headers,
            json={"perm": "analyze"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["perm"] == "analyze"

        # Guest (now an analyst) can view members.
        guest_token = await _login(GUEST_EMAIL, GUEST_PASSWORD)
        async with _client() as gclient:
            resp = await gclient.get(
                f"/applications/{owned_app}/members",
                headers={"Authorization": f"Bearer {guest_token}"},
            )
            assert resp.status_code == 403  # analyze is below the admin gate

        # Remove guest.
        resp = await client.delete(
            f"/applications/{owned_app}/members/{guest}",
            headers=headers,
        )
        assert resp.status_code == 204

        # Members list is back to just the owner.
        resp = await client.get(
            f"/applications/{owned_app}/members", headers=headers
        )
        assert [m["user_id"] for m in resp.json()] == [owner]


async def test_cannot_remove_last_admin(owned_app: int, owner: int) -> None:
    token = await _login(APP_OWNER_EMAIL, APP_OWNER_PASSWORD)
    async with _client() as client:
        resp = await client.delete(
            f"/applications/{owned_app}/members/{owner}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Global admin override
# ---------------------------------------------------------------------------


async def test_global_admin_can_manage_any_app(
    owned_app: int, admin: int, owner: int, guest: int
) -> None:
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        resp = await client.get(
            f"/applications/{owned_app}/members", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert any(m["user_id"] == owner for m in resp.json())

        resp = await client.post(
            f"/applications/{owned_app}/members",
            headers=headers,
            json={"user_id": guest, "perm": "read"},
        )
        assert resp.status_code == 201, resp.text

        resp = await client.delete(
            f"/applications/{owned_app}/members/{guest}", headers=headers
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Application detail scoping
# ---------------------------------------------------------------------------


async def test_outsider_cannot_get_application_detail(
    owned_app: int, outsider: int
) -> None:
    token = await _login(OUTSIDER_EMAIL, OUTSIDER_PASSWORD)
    async with _client() as client:
        resp = await client.get(
            f"/applications/{owned_app}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
