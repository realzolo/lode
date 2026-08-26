"""Application invariants and unified integration API contract."""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.api.routes import applications as application_routes
from lode.crypto import decrypt_secret
from lode.db.models.application import Application
from lode.db.models.integration import ApplicationIntegration
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

ADMIN_EMAIL = f"integration-admin-{uuid.uuid4().hex}@lode.local"
ADMIN_PASSWORD = "admin-pass-1"


@pytest_asyncio.fixture
async def admin() -> int:
    async with AsyncSessionLocal() as session:
        user = User(email=ADMIN_EMAIL, name="admin", role="admin", status="active")
        user.password_hash = hash_password(ADMIN_PASSWORD)
        session.add(user); await session.commit(); await session.refresh(user)
        user_id = user.id
    yield user_id
    async with AsyncSessionLocal() as session:
        row = await session.get(User, user_id)
        if row is not None:
            await session.delete(row); await session.commit()


async def _token() -> str:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        return response.json()["token"]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_application_create_requires_name_and_ingestion_topic(admin: int) -> None:
    token = await _token(); headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        missing = await client.post("/applications", headers=headers, json={"name": "checkout"})
        assert missing.status_code == 422
        topic = f"alerts-{uuid.uuid4().hex}"
        created = await client.post("/applications", headers=headers, json={"name": "checkout", "ingestion_topic": topic})
        assert created.status_code == 201, created.text
        assert created.json()["ingestion_topic"] == topic
        duplicate = await client.post("/applications", headers=headers, json={"name": "payments", "ingestion_topic": topic})
        assert duplicate.status_code == 409
        app_id = created.json()["id"]
    async with AsyncSessionLocal() as session:
        row = await session.get(Application, app_id)
        if row is not None:
            await session.delete(row); await session.commit()


async def test_topic_can_be_replaced_but_not_removed(admin: int) -> None:
    token = await _token(); headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        created = await client.post("/applications", headers=headers, json={"name": "topic-app", "ingestion_topic": f"old-{uuid.uuid4().hex}"})
        app_id = created.json()["id"]
        replacement = f"new-{uuid.uuid4().hex}"
        changed = await client.put(f"/applications/{app_id}/ingestion-topic", headers=headers, json={"ingestion_topic": replacement})
        assert changed.status_code == 200
        assert changed.json()["ingestion_topic"] == replacement
        removed = await client.put(f"/applications/{app_id}/ingestion-topic", headers=headers, json={"ingestion_topic": None})
        assert removed.status_code == 422
    async with AsyncSessionLocal() as session:
        row = await session.get(Application, app_id)
        if row is not None:
            await session.delete(row); await session.commit()


async def test_multiple_same_kind_integrations_and_encrypted_secrets(admin: int, monkeypatch) -> None:
    async def verified(_kind, _config, _secrets):
        return None
    monkeypatch.setattr(application_routes, "_verify_integration", verified)
    token = await _token(); headers = {"Authorization": f"Bearer {token}"}
    topic = f"integrations-{uuid.uuid4().hex}"
    base = {
        "kind": "database",
        "config": {"engine": "postgresql", "host": "db.example.com", "port": 5432, "database": "orders", "username": "readonly", "tls": True, "allowed_tables": ["public.orders"], "sensitive_columns": []},
        "secrets": {"password": "top-secret"},
    }
    async with _client() as client:
        created = await client.post("/applications", headers=headers, json={"name": "integration-app", "ingestion_topic": topic})
        app_id = created.json()["id"]
        first = await client.post(f"/applications/{app_id}/integrations", headers=headers, json={**base, "name": "orders-primary"})
        second = await client.post(f"/applications/{app_id}/integrations", headers=headers, json={**base, "name": "orders-replica"})
        assert first.status_code == second.status_code == 201
        detail = await client.get(f"/applications/{app_id}", headers=headers)
        assert [item["kind"] for item in detail.json()["integrations"]] == ["database", "database"]
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(ApplicationIntegration).where(ApplicationIntegration.application_id == app_id))).scalars().all()
        assert all("top-secret" not in row.secrets_ciphertext for row in rows)
        assert all("top-secret" in (decrypt_secret(row.secrets_ciphertext) or "") for row in rows)
        application = await session.get(Application, app_id)
        if application is not None:
            await session.delete(application); await session.commit()


async def test_indirect_secret_reference_is_rejected(admin: int) -> None:
    token = await _token(); headers = {"Authorization": f"Bearer {token}"}
    async with _client() as client:
        created = await client.post("/applications", headers=headers, json={"name": "secret-app", "ingestion_topic": f"secret-{uuid.uuid4().hex}"})
        app_id = created.json()["id"]
        response = await client.post(
            f"/applications/{app_id}/integrations", headers=headers,
            json={"name": "logs", "kind": "loki", "config": {"base_url": "https://logs.example.com", "limit": 100}, "secrets": {"bearer_token": "env:" + "//LOG_TOKEN"}},
        )
        assert response.status_code == 422
    async with AsyncSessionLocal() as session:
        application = await session.get(Application, app_id)
        if application is not None:
            await session.delete(application); await session.commit()


async def test_kind_catalog_exposes_capabilities_without_database_enum(admin: int) -> None:
    token = await _token()
    async with _client() as client:
        response = await client.get("/integration-kinds", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    kinds = {item["kind"]: item for item in response.json()}
    assert "redis" not in kinds
    assert "query_catalog" in kinds["database"]["capabilities"]
    assert "log_search" in kinds["loki"]["capabilities"]
