"""Integration tests for per-application configuration writes (admin only).

Drives the ASGI app with ``httpx.AsyncClient`` and exercises:
- 401 (no token)
- 403 (regular user)
- 404 (missing app / missing parent repo / missing binding)
- 409 (duplicate topic, duplicate repo binding)
- 200/201/204 happy paths under each admin endpoint
- that non-admin writes are gated even when the parent app exists

A fresh application (and a fresh global ``GitRepo`` for the repo-binding
tests) is created per test, so the suite is order-independent.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.api.routes import applications as application_routes
from lode.db.models.application import (
    Application,
    ApplicationDescription,
    ApplicationKafka,
    ApplicationRepo,
    DbSource,
)
from lode.db.models.git import GitRepo
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

ADMIN_EMAIL = f"app-admin-{uuid.uuid4().hex}@lode.local"
ADMIN_PASSWORD = "admin-pass-1"
USER_EMAIL = f"app-user-{uuid.uuid4().hex}@lode.local"
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
async def admin() -> int:
    uid = await _make_user(ADMIN_EMAIL, ADMIN_PASSWORD, "admin")
    yield uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


@pytest_asyncio.fixture
async def user() -> int:
    uid = await _make_user(USER_EMAIL, USER_PASSWORD, "user")
    yield uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


@pytest_asyncio.fixture
async def fresh_app(admin: int) -> int:
    """A fresh application owned by ``admin``; cascaded at teardown."""
    async with AsyncSessionLocal() as session:
        app_row = Application(name=f"app-{uuid.uuid4().hex}", created_by=admin)
        session.add(app_row)
        await session.commit()
        await session.refresh(app_row)
        app_id = app_row.id

    yield app_id

    # Cascade should clean child rows; we still do an explicit sweep so a test
    # that failed mid-way doesn't pollute the next one.
    async with AsyncSessionLocal() as session:
        v = await session.get(Application, app_id)
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


async def _make_git_repo(url: str | None = None) -> int:
    async with AsyncSessionLocal() as session:
        r = GitRepo(
            name=f"repo-{uuid.uuid4().hex}",
            repo_url=url or f"https://example.com/{uuid.uuid4().hex}.git",
            default_branch="main",
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
        return r.id


async def _cleanup_git_repo(repo_id: int) -> None:
    async with AsyncSessionLocal() as session:
        v = await session.get(GitRepo, repo_id)
        if v is not None:
            await session.delete(v)
            await session.commit()


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


async def test_unauthenticated_set_topic_rejected(fresh_app: int) -> None:
    async with _client() as client:
        resp = await client.put(
            f"/applications/{fresh_app}/topic",
            json={"topic": "anything"},
        )
        assert resp.status_code == 401


async def test_non_admin_set_topic_rejected(fresh_app: int, user: int) -> None:
    token = await _login(USER_EMAIL, USER_PASSWORD)
    async with _client() as client:
        resp = await client.put(
            f"/applications/{fresh_app}/topic",
            headers={"Authorization": f"Bearer {token}"},
            json={"topic": "anything"},
        )
        assert resp.status_code == 403


async def test_non_admin_bind_repo_rejected(fresh_app: int, user: int) -> None:
    repo_id = await _make_git_repo()
    try:
        token = await _login(USER_EMAIL, USER_PASSWORD)
        async with _client() as client:
            resp = await client.post(
                f"/applications/{fresh_app}/repos",
                headers={"Authorization": f"Bearer {token}"},
                json={"repo_id": repo_id, "description": ""},
            )
            assert resp.status_code == 403
    finally:
        await _cleanup_git_repo(repo_id)


# ---------------------------------------------------------------------------
# Kafka topic (admin)
# ---------------------------------------------------------------------------


async def test_admin_set_and_clear_topic(fresh_app: int, admin: int) -> None:
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        # initial bind
        resp = await client.put(
            f"/applications/{fresh_app}/topic",
            headers=headers,
            json={"topic": f"app-{uuid.uuid4().hex}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["application_id"] == fresh_app
        assert resp.json()["topic"]

        # replace (different topic)
        new_topic = f"app-{uuid.uuid4().hex}"
        resp = await client.put(
            f"/applications/{fresh_app}/topic",
            headers=headers,
            json={"topic": new_topic},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["topic"] == new_topic

        # clear (null payload)
        resp = await client.put(
            f"/applications/{fresh_app}/topic",
            headers=headers,
            json={"topic": None},
        )
        assert resp.status_code == 200
        assert resp.json()["topic"] is None

        # Confirm the DB row was removed on clear.
        async with AsyncSessionLocal() as session:
            row = await session.get(ApplicationKafka, fresh_app)
            assert row is None


async def test_admin_set_topic_409_on_duplicate(fresh_app: int, admin: int) -> None:
    """A topic bound to *another* application must surface as 409, not silently overwrite."""
    other_id = fresh_app
    # Create a second app and bind a topic to it.
    async with AsyncSessionLocal() as session:
        a2 = Application(name=f"other-{uuid.uuid4().hex}", created_by=admin)
        session.add(a2)
        await session.commit()
        await session.refresh(a2)
        second_id = a2.id

    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        topic = f"shared-{uuid.uuid4().hex}"
        async with _client() as client:
            # Bind on second app
            resp = await client.put(
                f"/applications/{second_id}/topic",
                headers=headers,
                json={"topic": topic},
            )
            assert resp.status_code == 200

            # Try to bind same topic on first app → 409
            resp = await client.put(
                f"/applications/{other_id}/topic",
                headers=headers,
                json={"topic": topic},
            )
            assert resp.status_code == 409
    finally:
        async with AsyncSessionLocal() as session:
            v = await session.get(Application, second_id)
            if v is not None:
                await session.delete(v)
                await session.commit()


async def test_admin_set_topic_missing_app(admin: int) -> None:
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        resp = await client.put(
            "/applications/99999999/topic",
            headers={"Authorization": f"Bearer {token}"},
            json={"topic": "x"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Repository binding (admin)
# ---------------------------------------------------------------------------


async def test_admin_bind_and_unbind_repo(fresh_app: int, admin: int) -> None:
    repo_id = await _make_git_repo()
    try:
        token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
        headers = {"Authorization": f"Bearer {token}"}
        async with _client() as client:
            # bind
            resp = await client.post(
                f"/applications/{fresh_app}/repos",
                headers=headers,
                json={"repo_id": repo_id, "description": "checkout service"},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["repo_id"] == repo_id
            assert body["description"] == "checkout service"
            assert body["repo_name"]

            # duplicate → 409
            resp = await client.post(
                f"/applications/{fresh_app}/repos",
                headers=headers,
                json={"repo_id": repo_id, "description": ""},
            )
            assert resp.status_code == 409

            # unbind
            resp = await client.delete(
                f"/applications/{fresh_app}/repos/{repo_id}",
                headers=headers,
            )
            assert resp.status_code == 204

            # unbind again → 404
            resp = await client.delete(
                f"/applications/{fresh_app}/repos/{repo_id}",
                headers=headers,
            )
            assert resp.status_code == 404

            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        select(ApplicationRepo).where(
                            ApplicationRepo.application_id == fresh_app,
                            ApplicationRepo.repo_id == repo_id,
                        )
                    )
                ).scalars().all()
                assert rows == []
    finally:
        await _cleanup_git_repo(repo_id)


async def test_admin_bind_unknown_repo_returns_404(fresh_app: int, admin: int) -> None:
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/repos",
            headers={"Authorization": f"Bearer {token}"},
            json={"repo_id": 999999999, "description": ""},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Data sources (admin)
# ---------------------------------------------------------------------------


async def test_admin_create_and_delete_db_source(fresh_app: int, admin: int, monkeypatch) -> None:
    async def verified(*_args, **_kwargs):
        return None

    monkeypatch.setattr(application_routes, "verify_postgres_readonly_account", verified)
    monkeypatch.setenv(
        "ORDERS_DSN", "postgresql://readonly@db.example.invalid/orders?sslmode=verify-full"
    )
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/db-sources",
            headers=headers,
            json={
                "name": "orders",
                "conn_secret_ref": "env://ORDERS_DSN",
                "allowed_tables": ["public.orders", "public.order_items"],
            },
        )
        assert resp.status_code == 201, resp.text
        source_id = resp.json()["id"]
        assert resp.json()["name"] == "orders"
        assert resp.json()["allowed_tables"] == ["public.orders", "public.order_items"]

        # delete
        resp = await client.delete(
            f"/applications/{fresh_app}/db-sources/{source_id}",
            headers=headers,
        )
        assert resp.status_code == 204

        async with AsyncSessionLocal() as session:
            row = await session.get(DbSource, source_id)
            assert row is None


async def test_admin_create_db_source_with_structured_fields(
    fresh_app: int, admin: int, monkeypatch
) -> None:
    """An admin can create a source by typing the connection in directly
    (structured host/port/database/username/password) instead of a secret ref.
    """
    async def verified(*_args, **_kwargs):
        return None

    monkeypatch.setattr(application_routes, "verify_postgres_readonly_account", verified)
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/db-sources",
            headers=headers,
            json={
                "name": "orders-db",
                "host": "10.0.0.5",
                "port": 5433,
                "database": "orders",
                "username": "readonly",
                "password": "super-secret",
                "sslmode": "verify-full",
                "allowed_tables": ["public.orders", "public.order_items"],
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "orders-db"
        assert body["host"] == "10.0.0.5"
        assert body["port"] == 5433
        assert body["database"] == "orders"
        assert body["username"] == "readonly"
        assert body["has_password"] is True
        # The raw password must never be echoed back to the client.
        assert "password" not in body
        assert body["allowed_tables"] == ["public.orders", "public.order_items"]

        async with AsyncSessionLocal() as session:
            row = await session.get(DbSource, body["id"])
            assert row is not None
            assert row.host == "10.0.0.5"
            # At rest the password is encrypted — the stored value is NOT the
            # plaintext, but it round-trips back to the original on decrypt.
            from lode.crypto import decrypt_secret

            assert row.password != "super-secret"
            assert decrypt_secret(row.password) == "super-secret"


async def test_admin_create_db_source_requires_connection(
    fresh_app: int, admin: int
) -> None:
    """Neither a secret ref nor structured fields -> 422 validation error."""
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/db-sources",
            headers=headers,
            json={"name": "broken", "allowed_tables": []},
        )
        assert resp.status_code == 422, resp.text


async def test_admin_create_db_source_requires_database_with_host(
    fresh_app: int, admin: int
) -> None:
    """Host without a database is rejected by the schema validator."""
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/db-sources",
            headers=headers,
            json={"name": "broken", "host": "10.0.0.5", "allowed_tables": []},
        )
        assert resp.status_code == 422, resp.text


async def test_admin_delete_db_source_wrong_app_returns_404(fresh_app: int, admin: int) -> None:
    """A source belonging to *another* application must not be deletable via
    this app's URL — prevents cross-app writes from leaking the resource ID."""
    # Create a second app + a source owned by it.
    async with AsyncSessionLocal() as session:
        a2 = Application(name=f"other-{uuid.uuid4().hex}", created_by=admin)
        session.add(a2)
        await session.commit()
        await session.refresh(a2)
        other_id = a2.id
        db = DbSource(
            application_id=a2.id,
            name="x",
            conn_secret_ref="env://X",
        )
        session.add(db)
        await session.commit()
        await session.refresh(db)
        stolen = db.id

    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    try:
        async with _client() as client:
            resp = await client.delete(
                f"/applications/{fresh_app}/db-sources/{stolen}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 404

            # The source on the other app is still alive
            async with AsyncSessionLocal() as session:
                row = await session.get(DbSource, stolen)
                assert row is not None
    finally:
        async with AsyncSessionLocal() as session:
            v = await session.get(Application, other_id)
            if v is not None:
                await session.delete(v)
                await session.commit()


async def test_admin_test_db_source_unreachable_returns_error(
    fresh_app: int, admin: int, monkeypatch
) -> None:
    """The pre-save ``/db-sources/test`` endpoint must surface a connection
    failure as a structured ``{ok: false, error}`` result — never a 500."""
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    monkeypatch.setenv(
        "UNREACHABLE_DSN", "postgresql://readonly@127.0.0.1:1/none?sslmode=verify-full"
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/db-sources/test",
            headers=headers,
            json={
                "name": "probe",
                "conn_secret_ref": "env://UNREACHABLE_DSN",
                "allowed_tables": ["public.probe"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert body["latency_ms"] is None
        assert body["error"]


# ---------------------------------------------------------------------------
# Descriptions (admin)
# ---------------------------------------------------------------------------


async def test_admin_create_and_delete_description(fresh_app: int, admin: int) -> None:
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/descriptions",
            headers=headers,
            json={"description_type": "deploy", "content": "Deployed at noon UTC"},
        )
        assert resp.status_code == 201, resp.text
        description_id = resp.json()["id"]
        assert resp.json()["description_type"] == "deploy"
        assert resp.json()["content"] == "Deployed at noon UTC"

        # delete
        resp = await client.delete(
            f"/applications/{fresh_app}/descriptions/{description_id}",
            headers=headers,
        )
        assert resp.status_code == 204

        async with AsyncSessionLocal() as session:
            row = await session.get(ApplicationDescription, description_id)
            assert row is None


async def test_admin_create_description_rejects_bad_type(fresh_app: int, admin: int) -> None:
    """``description_type`` is constrained to ``deploy|other`` by the DB CheckConstraint
    *and* the pydantic schema's regex; here we rely on the schema (422).
    """
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            f"/applications/{fresh_app}/descriptions",
            headers={"Authorization": f"Bearer {token}"},
            json={"description_type": "evil", "content": "x"},
        )
        assert resp.status_code == 422
