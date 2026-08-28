"""Database-backed control-plane authorization and secret-redaction tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.api.routes import control_plane
from lode.config import settings
from lode.db.models import (
    GitAccount,
    GitAccountRepositoryAccess,
    GitRepository,
    User,
    Workspace,
    WorkspacePermission,
)
from lode.db.session import AsyncSessionLocal
from lode.security import create_token, hash_password


async def _admin_headers() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        admin = await session.scalar(select(User).where(User.is_system_admin))
        assert admin is not None
        admin.must_change_password = False
        await session.commit()
        admin_id = admin.id
    return {
        "Authorization": "Bearer "
        + create_token(admin_id, settings.jwt_signing_key, 3600)
    }


@pytest.mark.asyncio
async def test_workspace_topic_changes_require_a_non_active_workspace() -> None:
    suffix = uuid.uuid4().hex
    headers = await _admin_headers()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/workspaces",
            headers=headers,
            json={"name": f"topic-first-{suffix}", "ingestion_topic": f"topic-a-{suffix}"},
        )
        second = await client.post(
            "/workspaces",
            headers=headers,
            json={"name": f"topic-second-{suffix}", "ingestion_topic": f"topic-b-{suffix}"},
        )
        assert first.status_code == second.status_code == 201
        first_id = first.json()["id"]
        second_id = second.json()["id"]

        draft_changed = await client.patch(
            f"/workspaces/{first_id}",
            headers=headers,
            json={"ingestion_topic": f"topic-c-{suffix}"},
        )
        assert draft_changed.status_code == 200
        assert draft_changed.json()["ingestion_topic"] == f"topic-c-{suffix}"
        assert draft_changed.json()["ingestion_state"] == "draft"
        assert draft_changed.json()["ingestion_start_position"] is None

        conflict = await client.patch(
            f"/workspaces/{first_id}",
            headers=headers,
            json={"ingestion_topic": f"topic-b-{suffix}"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "workspace_topic_conflict"

    async with AsyncSessionLocal() as session:
        workspace = await session.get(Workspace, first_id)
        assert workspace is not None
        workspace.ingestion_state = "paused"
        workspace.ingestion_start_position = "latest"
        workspace.ingestion_activation_kind = "resume"
        workspace.ingestion_started_at = datetime.now(UTC)
        workspace.ingestion_paused_at = datetime.now(UTC)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        paused_changed = await client.patch(
            f"/workspaces/{first_id}",
            headers=headers,
            json={"ingestion_topic": f"topic-d-{suffix}"},
        )
        assert paused_changed.status_code == 200
        assert paused_changed.json()["ingestion_state"] == "draft"
        assert paused_changed.json()["ingestion_start_position"] is None

    async with AsyncSessionLocal() as session:
        workspace = await session.get(Workspace, second_id)
        assert workspace is not None
        workspace.ingestion_state = "active"
        workspace.ingestion_start_position = "earliest"
        workspace.ingestion_activation_kind = "start"
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.patch(
            f"/workspaces/{second_id}",
            headers=headers,
            json={"ingestion_topic": f"topic-e-{suffix}"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "ingestion_topic_change_requires_pause"

        profile_only = await client.patch(
            f"/workspaces/{second_id}",
            headers=headers,
            json={"name": f"renamed-{suffix}", "description": "Still editable while active."},
        )
        assert profile_only.status_code == 200
        assert profile_only.json()["name"] == f"renamed-{suffix}"


@pytest.mark.asyncio
async def test_repository_binding_uses_account_repository_access_directly() -> None:
    suffix = uuid.uuid4().hex
    headers = await _admin_headers()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace = await client.post(
            "/workspaces",
            headers=headers,
            json={"name": f"repository-{suffix}", "ingestion_topic": f"repository-{suffix}"},
        )
        assert workspace.status_code == 201
        workspace_id = workspace.json()["id"]

    async with AsyncSessionLocal() as session:
        account = GitAccount(
            adapter_id="github",
            api_url="https://api.github.invalid",
            endpoint_identity_hash=suffix[:64].ljust(64, "a"),
            name=f"account-{suffix}",
            external_account_id=suffix,
            external_account_login=f"login-{suffix}",
            account_url=f"https://github.invalid/{suffix}",
            verification_status="healthy",
            verified_at=datetime.now(UTC),
        )
        repository = GitRepository(
            adapter_id="github",
            endpoint_identity_hash=account.endpoint_identity_hash,
            external_repository_id=suffix,
            name="checkout",
            full_name=f"example/checkout-{suffix}",
            repo_url=f"https://github.invalid/example/checkout-{suffix}.git",
            web_url=f"https://github.invalid/example/checkout-{suffix}",
            visibility="private",
        )
        session.add_all([account, repository])
        await session.flush()
        access = GitAccountRepositoryAccess(
            account_connection_id=account.id,
            repository_id=repository.id,
            access_level="read",
            state="available",
            last_seen_at=datetime.now(UTC),
        )
        session.add(access)
        await session.commit()
        account_id = account.id
        repository_id = repository.id

    payload = {
        "account_connection_id": account_id,
        "repository_id": repository_id,
        "role": "runtime_source",
        "priority": 0,
        "description": "Primary runtime source",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            f"/workspaces/{workspace_id}/repositories", headers=headers, json=payload
        )
        assert created.status_code == 201
        assert created.json()["account_connection_id"] == account_id
        assert created.json()["repository_id"] == repository_id
        assert created.json()["account_name"] == f"account-{suffix}"
        assert created.json()["external_account_login"] == f"login-{suffix}"
        assert created.json()["branch_mode"] == "default"
        assert created.json()["branch_name"] is None
        assert created.json()["effective_branch"] == "main"

        edited = await client.patch(
            f"/workspaces/{workspace_id}/repositories/{created.json()['id']}",
            headers=headers,
            json={
                "expected_revision": created.json()["revision"],
                "role": "shared_library",
                "priority": 5,
                "description": "Shared checkout utilities",
            },
        )
        assert edited.status_code == 200
        assert edited.json()["role"] == "shared_library"
        assert edited.json()["revision"] == created.json()["revision"] + 1

        stale = await client.patch(
            f"/workspaces/{workspace_id}/repositories/{created.json()['id']}",
            headers=headers,
            json={"expected_revision": created.json()["revision"], "priority": 7},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "repository_binding_revision_conflict"

        disabled = await client.delete(
            f"/workspaces/{workspace_id}/repositories/{created.json()['id']}?expected_revision={edited.json()['revision']}",
            headers=headers,
        )
        assert disabled.status_code == 204
        restored = await client.patch(
            f"/workspaces/{workspace_id}/repositories/{created.json()['id']}",
            headers=headers,
            json={"expected_revision": edited.json()["revision"] + 1, "state": "active"},
        )
        assert restored.status_code == 200
        assert restored.json()["state"] == "active"

        duplicate = await client.post(
            f"/workspaces/{workspace_id}/repositories", headers=headers, json=payload
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "repository_binding_conflict"

    async with AsyncSessionLocal() as session:
        access = await session.get(GitAccountRepositoryAccess, (account_id, repository_id))
        assert access is not None
        access.state = "lost"
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        lost = await client.post(
            f"/workspaces/{workspace_id}/repositories", headers=headers, json=payload
        )
        assert lost.status_code == 409
        assert lost.json()["error"]["code"] == "repository_access_lost"
        branch_catalogue = await client.get(
            f"/git-accounts/{account_id}/repositories/{repository_id}/branches",
            headers=headers,
        )
        assert branch_catalogue.status_code == 409
        assert branch_catalogue.json()["error"]["code"] == "repository_access_lost"


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
        return ("gpt-5.6-sol",)

    async def probe(*_args, **_kwargs):
        return True, None

    monkeypatch.setattr(control_plane, "_discover_provider_models", discover)
    monkeypatch.setattr(control_plane, "_probe_model", probe)
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
                "provider_kind": "openai",
                "protocol_id": "openai.responses.v1",
                "base_url": "https://api.openai.com/v1",
                "api_key": secret,
                "models": [{"provider_model_id": "gpt-5.6-sol", "source": "discovered"}],
            },
        )
        assert provider.status_code == 201
        assert secret not in provider.text
        assert "api_key" not in provider.json()

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
        assert workspace.json()["description"] == ""
        assert workspace.json()["architecture_context_revision_id"] is not None

        context = await client.get(
            f"/workspaces/{workspace_id}/architecture-context", headers=admin_headers
        )
        assert context.status_code == 200
        assert context.json()["entries"] == []
        updated_context = await client.put(
            f"/workspaces/{workspace_id}/architecture-context",
            headers=admin_headers,
            json={
                "entries": [
                    {
                        "kind": "system_purpose",
                        "title": "Payments",
                        "content": "Owns payment incident investigation.",
                    }
                ]
            },
        )
        assert updated_context.status_code == 200
        assert updated_context.json()["revision"] == 2

        async def topic_exists(_topic: str) -> bool:
            return True

        monkeypatch.setattr(control_plane, "_broker_has_topic", topic_exists)
        readiness = await client.get(
            f"/workspaces/{workspace_id}/readiness", headers=admin_headers
        )
        assert readiness.status_code == 200
        assert readiness.json()["can_start"] is False
        assert {item["code"]: item["outcome"] for item in readiness.json()["checks"]} == {
            "kafka_topic": "passed",
            "model_policy": "blocked",
            "repositories": "warning",
            "evidence_connectors": "warning",
            "architecture_context": "passed",
        }
        blocked_start = await client.post(
            f"/workspaces/{workspace_id}/ingestion/start",
            headers=admin_headers,
            json={"start_position": "latest"},
        )
        assert blocked_start.status_code == 409
        assert blocked_start.json()["error"]["code"] == "workspace_not_ready"
        assert blocked_start.json()["error"]["details"]["blockers"][0]["code"] == "model_policy"

        admin_workspaces = await client.get("/workbench/workspaces", headers=admin_headers)
        assert admin_workspaces.status_code == 200
        assert workspace_id in {item["id"] for item in admin_workspaces.json()}

        admin_components = await client.get(
            f"/workspaces/{workspace_id}/components", headers=admin_headers
        )
        assert admin_components.status_code == 200

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
        return tuple(sorted(upstream_ids))

    async def probe(*_args, **_kwargs):
        return True, None

    monkeypatch.setattr(control_plane, "_discover_provider_models", discover)
    monkeypatch.setattr(control_plane, "_probe_model", probe)
    headers = {"Authorization": "Bearer " + create_token(admin_id, settings.jwt_signing_key, 3600)}
    secret = f"draft-secret-{suffix}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        draft = await client.post(
            "/ai-provider-accounts/discover-models",
            headers=headers,
            json={
                "provider_kind": "openai",
                "protocol_id": "openai.responses.v1",
                "base_url": "https://api.openai.com/v1",
                "api_key": secret,
            },
        )
        assert draft.status_code == 200
        assert secret not in draft.text
        assert set(draft.json()["available_model_ids"]) == upstream_ids

        created = await client.post(
            "/ai-provider-accounts",
            headers=headers,
            json={
                "name": f"account-{suffix}",
                "provider_kind": "openai",
                "protocol_id": "openai.responses.v1",
                "base_url": "https://api.openai.com/v1",
                "api_key": secret,
                "models": [
                    {"provider_model_id": model_id, "source": "discovered"}
                    for model_id in sorted(upstream_ids)
                ],
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
            json={"models": [{"provider_model_id": "gpt-5.6-terra", "source": "discovered"}]},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "account_model_in_use"

        upstream_ids.remove("gpt-5.6-sol")
        missing = await client.put(
            f"/ai-provider-accounts/{account_id}/models",
            headers=headers,
            json={"models": [
                {"provider_model_id": "gpt-5.6-sol", "source": "discovered"},
                {"provider_model_id": "gpt-5.6-terra", "source": "discovered"},
            ]},
        )
        assert missing.status_code == 200
        sol = next(item for item in missing.json()["models"] if item["provider_model_id"] == "gpt-5.6-sol")
        assert sol["discovery_state"] == "missing"
        assert sol["state"] == "disabled"

        upstream_ids.clear()
        refreshed = await client.post(
            f"/ai-provider-accounts/{account_id}/discover-models",
            headers=headers,
        )
        assert refreshed.status_code == 200
        accounts = await client.get("/ai-provider-accounts", headers=headers)
        assert accounts.status_code == 200
        refreshed_account = next(item for item in accounts.json() if item["id"] == account_id)
        assert refreshed_account["verification_status"] == "unavailable"
        assert all(item["state"] == "disabled" for item in refreshed_account["models"])

        manual = await client.post(
            "/ai-provider-accounts",
            headers=headers,
            json={
                "name": f"manual-{suffix}",
                "provider_kind": "openai",
                "protocol_id": "openai.responses.v1",
                "base_url": "https://api.openai.com/v1",
                "api_key": f"manual-secret-{suffix}",
                "models": [{"provider_model_id": "gpt-5.6-luna", "source": "manual"}],
            },
        )
        assert manual.status_code == 201
        assert manual.json()["models"][0]["discovery_state"] == "manual"
        unknown = await client.put(
            f"/ai-provider-accounts/{account_id}/models",
            headers=headers,
            json={"models": [{"provider_model_id": "not-supported", "source": "manual"}]},
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "unsupported_model"
