"""Database-backed control-plane authorization and secret-redaction tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.api.routes import control_plane
from lode.config import settings
from lode.db.models import User, WorkspacePermission
from lode.db.session import AsyncSessionLocal
from lode.security import create_token, hash_password


@pytest.mark.asyncio
async def test_control_plane_redacts_secrets_and_enforces_workspace_permissions(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        admin = await session.scalar(select(User).where(User.is_system_admin))
        assert admin is not None
        admin.must_change_password = False
        reader = User(
            username=f"reader-{suffix[:12]}",
            display_name="Control Reader",
            password_hash=hash_password("correct-horse-battery"),
            status="active",
            must_change_password=False,
        )
        session.add_all([admin, reader])
        await session.commit()
        admin_id = admin.id
        reader_id = reader.id

    secret = f"provider-secret-{suffix}"
    async def discover(**_kwargs):
        return control_plane._DiscoveredModels([{"id": "gpt-5.6-sol", "owned_by": "openai"}])

    monkeypatch.setattr(control_plane, "_discover_provider_models", discover)
    admin_headers = {
        "Authorization": "Bearer "
        + create_token(admin_id, settings.jwt_signing_key, 3600)
    }
    reader_headers = {
        "Authorization": "Bearer "
        + create_token(reader_id, settings.jwt_signing_key, 3600)
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider = await client.post(
            "/ai-provider-accounts",
            headers=admin_headers,
            json={
                "name": f"provider-{suffix}",
                "base_url": "https://api.openai.com/v1",
                "credential": secret,
                "model_ids": ["gpt-5.6-sol"],
                "manual_model_ids": [],
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

        hidden = await client.get("/workbench/workspaces", headers=reader_headers)
        assert hidden.status_code == 200
        assert workspace_id not in {item["id"] for item in hidden.json()}

        forbidden = await client.get(f"/workspaces/{workspace_id}", headers=reader_headers)
        assert forbidden.status_code == 403

    async with AsyncSessionLocal() as session:
        session.add(
            WorkspacePermission(
                user_id=reader_id,
                workspace_id=workspace_id,
                permission="viewer",
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        visible = await client.get("/workbench/workspaces", headers=reader_headers)
        assert visible.status_code == 200
        assert workspace_id in {item["id"] for item in visible.json()}

        still_forbidden = await client.post(
            f"/workspaces/{workspace_id}/ingestion/start",
            headers=reader_headers,
            json={"start_position": "latest"},
        )
        assert still_forbidden.status_code == 403


@pytest.mark.asyncio
async def test_account_model_sync_manual_selection_and_active_binding_guard(monkeypatch) -> None:
    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        admin = await session.scalar(select(User).where(User.is_system_admin))
        assert admin is not None
        admin.must_change_password = False
        await session.commit()
        admin_id = admin.id

    upstream_ids = {"gpt-5.6-sol", "gpt-5.6-terra"}

    async def discover(**_kwargs):
        return control_plane._DiscoveredModels(
            [{"id": model_id, "owned_by": "openai"} for model_id in sorted(upstream_ids)]
        )

    monkeypatch.setattr(control_plane, "_discover_provider_models", discover)
    headers = {"Authorization": "Bearer " + create_token(admin_id, settings.jwt_signing_key, 3600)}
    secret = f"draft-secret-{suffix}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        draft = await client.post(
            "/ai-provider-accounts/discover-models",
            headers=headers,
            json={"base_url": "https://api.openai.com/v1", "credential": secret},
        )
        assert draft.status_code == 200
        assert secret not in draft.text
        assert {item["provider_model_id"] for item in draft.json()} == upstream_ids

        created = await client.post(
            "/ai-provider-accounts",
            headers=headers,
            json={
                "name": f"account-{suffix}",
                "base_url": "https://api.openai.com/v1",
                "credential": secret,
                "model_ids": sorted(upstream_ids),
                "manual_model_ids": [],
            },
        )
        assert created.status_code == 201
        account = created.json()
        account_id = account["id"]
        sol_id = next(item["id"] for item in account["models"] if item["provider_model_id"] == "gpt-5.6-sol")

        workspace = await client.post(
            "/workspaces",
            headers=headers,
            json={"name": f"models-{suffix}", "ingestion_topic": f"models-{suffix}"},
        )
        assert workspace.status_code == 201
        binding = await client.post(
            f"/workspaces/{workspace.json()['id']}/model-bindings",
            headers=headers,
            json={
                "provider_account_model_id": sol_id,
                "execution_classes": ["latency_optimized"],
                "allowed_roles": ["planner"],
                "max_calls": 1,
                "max_cost_per_call": 0,
                "timeout_ms": 1000,
                "allowed_data_classes": ["masked"],
                "max_context_utilization": 0.8,
            },
        )
        assert binding.status_code == 201

        blocked = await client.put(
            f"/ai-provider-accounts/{account_id}/models",
            headers=headers,
            json={"model_ids": ["gpt-5.6-terra"], "manual_model_ids": []},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "account_model_in_use"

        upstream_ids.remove("gpt-5.6-sol")
        missing = await client.put(
            f"/ai-provider-accounts/{account_id}/models",
            headers=headers,
            json={"model_ids": ["gpt-5.6-sol", "gpt-5.6-terra"], "manual_model_ids": []},
        )
        assert missing.status_code == 200
        sol = next(item for item in missing.json()["models"] if item["provider_model_id"] == "gpt-5.6-sol")
        assert sol["discovery_state"] == "missing"
        assert sol["state"] == "disabled"

        manual = await client.post(
            "/ai-provider-accounts",
            headers=headers,
            json={
                "name": f"manual-{suffix}",
                "base_url": "https://api.openai.com/v1",
                "credential": f"manual-secret-{suffix}",
                "model_ids": ["gpt-5.6-luna"],
                "manual_model_ids": ["gpt-5.6-luna"],
            },
        )
        assert manual.status_code == 201
        assert manual.json()["models"][0]["discovery_state"] == "manual"
        unknown = await client.put(
            f"/ai-provider-accounts/{account_id}/models",
            headers=headers,
            json={"model_ids": ["not-supported"], "manual_model_ids": ["not-supported"]},
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "unsupported_model"
