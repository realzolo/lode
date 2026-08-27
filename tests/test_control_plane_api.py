"""Database-backed control-plane authorization and secret-redaction tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from lode.api.main import app
from lode.db.models import User, WorkspacePermission
from lode.db.session import AsyncSessionLocal
from lode.security import create_token, hash_password


@pytest.mark.asyncio
async def test_control_plane_redacts_secrets_and_enforces_workspace_permissions() -> None:
    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        admin = User(
            email=f"control-admin-{suffix}@lode.local",
            name="Control Admin",
            password_hash=hash_password("correct-horse-battery"),
            role="admin",
            status="active",
        )
        reader = User(
            email=f"control-reader-{suffix}@lode.local",
            name="Control Reader",
            password_hash=hash_password("correct-horse-battery"),
            role="user",
            status="active",
        )
        session.add_all([admin, reader])
        await session.commit()
        admin_id = admin.id
        reader_id = reader.id

    secret = f"provider-secret-{suffix}"
    admin_headers = {
        "Authorization": "Bearer "
        + create_token(admin_id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 3600)
    }
    reader_headers = {
        "Authorization": "Bearer "
        + create_token(reader_id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 3600)
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider = await client.post(
            "/ai-provider-accounts",
            headers=admin_headers,
            json={
                "name": f"provider-{suffix}",
                "provider_kind": "anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "credential": secret,
                "rate_limit_policy": {},
                "cost_policy": {},
                "data_processing_policy_revision": "policy-current",
                "data_residency": "global",
                "retention_mode": "provider-default",
            },
        )
        assert provider.status_code == 201
        assert secret not in provider.text
        assert "credential" not in provider.json()

        provider_list = await client.get("/ai-provider-accounts", headers=admin_headers)
        assert provider_list.status_code == 200
        assert secret not in provider_list.text

        workspace = await client.post(
            "/workspaces",
            headers=admin_headers,
            json={"name": f"workspace-{suffix}", "ingestion_topic": f"topic-{suffix}"},
        )
        assert workspace.status_code == 201
        workspace_id = workspace.json()["id"]

        hidden = await client.get("/workspaces", headers=reader_headers)
        assert hidden.status_code == 200
        assert workspace_id not in {item["id"] for item in hidden.json()}

        forbidden = await client.get(f"/workspaces/{workspace_id}", headers=reader_headers)
        assert forbidden.status_code == 403

    async with AsyncSessionLocal() as session:
        session.add(
            WorkspacePermission(
                user_id=reader_id,
                workspace_id=workspace_id,
                permission="read",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        visible = await client.get(f"/workspaces/{workspace_id}", headers=reader_headers)
        assert visible.status_code == 200

        still_forbidden = await client.post(
            f"/workspaces/{workspace_id}/ingestion/start",
            headers=reader_headers,
            json={"start_position": "latest"},
        )
        assert still_forbidden.status_code == 403
