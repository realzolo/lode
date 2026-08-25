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
from datetime import UTC, datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.api.routes import applications as application_routes
from lode.api.routes import investigations as investigation_routes
from lode.db.models.ai_model import AiModelConfig
from lode.db.models.application import (
    Application,
    ApplicationDescription,
    ApplicationKafka,
    ApplicationRepo,
    DbSource,
)
from lode.db.models.git import GitRepo
from lode.db.models.investigation import Investigation
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine.model_health import ModelHealth
from lode.engine.investigation_intake import create_investigation
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
# Ingestion activation readiness
# ---------------------------------------------------------------------------


async def test_model_binding_probes_protocol_before_persisting(
    fresh_app: int,
    admin: int,
    monkeypatch,
) -> None:
    async with AsyncSessionLocal() as session:
        model = AiModelConfig(
            provider="openai",
            base_url="https://model.example",
            api_key_ref="env://LODE_TEST_MODEL_KEY",
            model="test-model",
            is_default=False,
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)
        model_id = model.id

    async def unavailable(_model: AiModelConfig) -> ModelHealth:
        return ModelHealth(False, "https://model.example/v1/chat/completions", 21, "http_401", "Provider rejected the API key.")

    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        monkeypatch.setattr(application_routes, "probe_model", unavailable)
        async with _client() as client:
            response = await client.put(
                f"/applications/{fresh_app}/model",
                headers=headers,
                json={"model_config_id": model_id},
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "model_unavailable"

        async with AsyncSessionLocal() as session:
            application = await session.get(Application, fresh_app)
            checked_model = await session.get(AiModelConfig, model_id)
            assert application is not None and application.model_config_id is None
            assert checked_model is not None and checked_model.last_test_status == "unavailable"

        async def available(_model: AiModelConfig) -> ModelHealth:
            return ModelHealth(True, "https://model.example/v1/chat/completions", 18, None, None)

        monkeypatch.setattr(application_routes, "probe_model", available)
        async with _client() as client:
            response = await client.put(
                f"/applications/{fresh_app}/model",
                headers=headers,
                json={"model_config_id": model_id},
            )
            assert response.status_code == 200, response.text
            assert response.json()["model_test"]["available"] is True

        async with AsyncSessionLocal() as session:
            application = await session.get(Application, fresh_app)
            checked_model = await session.get(AiModelConfig, model_id)
            assert application is not None and application.model_config_id == model_id
            assert checked_model is not None and checked_model.last_test_status == "available"
    finally:
        async with AsyncSessionLocal() as session:
            application = await session.get(Application, fresh_app)
            if application is not None:
                application.model_config_id = None
            await session.flush()
            model = await session.get(AiModelConfig, model_id)
            if model is not None:
                await session.delete(model)
            await session.commit()


async def test_start_requires_repository_topic_and_model(
    fresh_app: int,
    admin: int,
    monkeypatch,
) -> None:
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    repo_id = await _make_git_repo()
    model_id: int | None = None
    validated_topics: list[str] = []

    async def validate_topic(topic: str) -> None:
        validated_topics.append(topic)

    monkeypatch.setattr(application_routes, "_validate_kafka_topic", validate_topic)
    topic = f"ready-{uuid.uuid4().hex}"
    try:
        async with _client() as client:
            response = await client.post(
                f"/applications/{fresh_app}/ingestion/start",
                headers=headers,
                json={"start_position": "latest"},
            )
            assert response.status_code == 409
            assert response.json()["error"] == {
                "code": "application_not_ready",
                "message": "Complete all required application settings before starting ingestion.",
                "details": {"missing": ["repositories", "topic", "model"]},
            }
            assert validated_topics == []

            async with AsyncSessionLocal() as session:
                session.add(ApplicationRepo(application_id=fresh_app, repo_id=repo_id))
                await session.commit()
            response = await client.post(
                f"/applications/{fresh_app}/ingestion/start",
                headers=headers,
                json={"start_position": "latest"},
            )
            assert response.status_code == 409
            assert response.json()["error"]["details"]["missing"] == ["topic", "model"]

            async with AsyncSessionLocal() as session:
                session.add(ApplicationKafka(application_id=fresh_app, topic=topic))
                await session.commit()
            response = await client.post(
                f"/applications/{fresh_app}/ingestion/start",
                headers=headers,
                json={"start_position": "latest"},
            )
            assert response.status_code == 409
            assert response.json()["error"]["details"]["missing"] == ["model"]

            async with AsyncSessionLocal() as session:
                model = AiModelConfig(
                    provider="openai",
                    base_url="https://api.openai.com/v1",
                    api_key_ref="env://LODE_TEST_MODEL_KEY",
                    model="test-model",
                    is_default=False,
                )
                session.add(model)
                await session.flush()
                application = await session.get(Application, fresh_app)
                assert application is not None
                application.model_config_id = model.id
                model_id = model.id
                await session.commit()

            listed = await client.get("/applications", headers=headers)
            assert listed.status_code == 200
            listed_app = next(row for row in listed.json() if row["id"] == fresh_app)
            assert listed_app["repo_count"] == 1
            assert listed_app["model_configured"] is True
            assert listed_app["model_available"] is False

            response = await client.post(
                f"/applications/{fresh_app}/ingestion/start",
                headers=headers,
                json={"start_position": "latest"},
            )
            assert response.status_code == 409
            assert response.json()["error"]["details"]["missing"] == ["model_availability"]

            async with AsyncSessionLocal() as session:
                model = await session.get(AiModelConfig, model_id)
                assert model is not None
                model.last_test_status = "available"
                await session.commit()

            response = await client.post(
                f"/applications/{fresh_app}/ingestion/start",
                headers=headers,
                json={"start_position": "latest"},
            )
            assert response.status_code == 202, response.text
            assert response.json()["desired_state"] == "active"
            assert validated_topics == [topic]
    finally:
        async with AsyncSessionLocal() as session:
            application = await session.get(Application, fresh_app)
            if application is not None:
                application.model_config_id = None
            binding = await session.scalar(
                select(ApplicationRepo).where(
                    ApplicationRepo.application_id == fresh_app,
                    ApplicationRepo.repo_id == repo_id,
                )
            )
            if binding is not None:
                await session.delete(binding)
            await session.flush()
            if model_id is not None:
                model = await session.get(AiModelConfig, model_id)
                if model is not None:
                    await session.delete(model)
            await session.commit()
        await _cleanup_git_repo(repo_id)


async def test_resume_rechecks_required_configuration(
    fresh_app: int,
    admin: int,
) -> None:
    async with AsyncSessionLocal() as session:
        application = await session.get(Application, fresh_app)
        assert application is not None
        application.ingestion_state = "paused"
        application.ingestion_start_position = "latest"
        await session.commit()

    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        response = await client.post(
            f"/applications/{fresh_app}/ingestion/resume",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 409
    assert response.json()["error"]["details"]["missing"] == [
        "repositories",
        "topic",
        "model",
    ]


async def test_terminal_investigation_can_retry_then_become_permanently_read_only(
    fresh_app: int,
    admin: int,
    monkeypatch,
) -> None:
    async with AsyncSessionLocal() as session:
        model = AiModelConfig(
            provider="openai",
            base_url="https://model.example",
            api_key_ref="env://LODE_TEST_MODEL_KEY",
            model="test-model",
            is_default=False,
            last_test_status="available",
        )
        session.add(model)
        await session.flush()
        application = await session.get(Application, fresh_app)
        assert application is not None
        application.model_config_id = model.id
        original, original_job = await create_investigation(
            session,
            application_id=fresh_app,
            trigger_signature=uuid.uuid4().hex,
            source_type="manual",
            title="retry lifecycle",
            severity="CRITICAL",
            occurred_at=datetime.now(UTC),
            output_language="zh",
            error_name="GatewayError",
            error_message="payment failed",
            fields={"code": "PAYMENT_FAILED"},
            created_by=admin,
        )
        original.status = "completed"
        original.result_state = "unavailable"
        original.finished_at = datetime.now(UTC)
        original_job.status = "succeeded"
        original_job.finished_at = datetime.now(UTC)
        await session.commit()
        original_id = original.public_id
        model_id = model.id

    async def available(_model: AiModelConfig) -> ModelHealth:
        return ModelHealth(True, "https://model.example/v1/chat/completions", 12, None, None)

    monkeypatch.setattr(investigation_routes, "probe_model", available)
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with _client() as client:
            retried = await client.post(f"/investigations/{original_id}/retry", headers=headers)
            assert retried.status_code == 202, retried.text
            assert retried.json()["retry_of"] == original_id
            retry_id = retried.json()["id"]

            retry_detail = await client.get(f"/investigations/{retry_id}", headers=headers)
            assert retry_detail.status_code == 200
            assert retry_detail.json()["retry_of"] == original_id

            archived = await client.post(f"/investigations/{original_id}/archive", headers=headers)
            assert archived.status_code == 200
            assert archived.json()["read_only"] is True

            rejected = await client.post(f"/investigations/{original_id}/retry", headers=headers)
            assert rejected.status_code == 409
            assert "read-only" in rejected.json()["error"]["message"]

        async with AsyncSessionLocal() as session:
            original = (
                await session.execute(select(Investigation).where(Investigation.public_id == original_id))
            ).scalars().one()
            assert original.archived_at is not None
            assert original.archived_by == admin
    finally:
        async with AsyncSessionLocal() as session:
            application = await session.get(Application, fresh_app)
            if application is not None:
                application.model_config_id = None
            await session.flush()
            model = await session.get(AiModelConfig, model_id)
            if model is not None:
                await session.delete(model)
            await session.commit()


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
