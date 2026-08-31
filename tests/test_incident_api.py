"""Incident-first operational API tests."""

from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from lode.api.main import app
from lode.config import settings
from lode.db.models import (
    User,
    Workspace,
    WorkspaceArchitectureContextRevision,
    WorkspacePermission,
)
from lode.db.session import AsyncSessionLocal
from lode.security import create_token, hash_password


def _manual_payload() -> dict[str, object]:
    return {
        "schema_version": "manual-incident.v1",
        "summary": "Checkout payment failed",
        "error_text": "GatewayError: payment gateway failed\n  at gateway.py:1",
        "trace_id": "incident-trace",
        "repository_binding_id": None,
    }


@pytest.mark.asyncio
async def test_manual_incident_api_correlates_signals_and_enforces_server_capabilities() -> None:
    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        operator = User(
            username=f"incident-operator-{suffix[:10]}",
            display_name="Incident Operator",
            password_hash=hash_password("correct-horse-battery"),
            status="active",
            must_change_password=False,
        )
        viewer = User(
            username=f"incident-viewer-{suffix[:10]}",
            display_name="Incident Viewer",
            password_hash=hash_password("correct-horse-battery"),
            status="active",
            must_change_password=False,
        )
        workspace = Workspace(name=f"incident-{suffix}", ingestion_topic=f"incident-{suffix}")
        session.add_all([operator, viewer, workspace])
        await session.flush()
        architecture_context = WorkspaceArchitectureContextRevision(
            workspace_id=workspace.id,
            entries=[],
            revision=1,
            created_by=operator.id,
        )
        session.add(architecture_context)
        await session.flush()
        workspace.architecture_context_revision_id = architecture_context.id
        session.add_all(
            [
                WorkspacePermission(
                    workspace_id=workspace.id, user_id=operator.id, permission="operator"
                ),
                WorkspacePermission(
                    workspace_id=workspace.id, user_id=viewer.id, permission="viewer"
                ),
            ]
        )
        await session.commit()
        workspace_id = workspace.id
        operator_id = operator.id
        viewer_id = viewer.id

    operator_headers = {
        "Authorization": "Bearer " + create_token(operator_id, settings.jwt_signing_key, 3600)
    }
    viewer_headers = {
        "Authorization": "Bearer " + create_token(viewer_id, settings.jwt_signing_key, 3600)
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            f"/workspaces/{workspace_id}/manual-incidents",
            headers={**operator_headers, "Idempotency-Key": f"manual-{suffix}-first"},
            json=_manual_payload(),
        )
        assert first.status_code == 201
        first_body = first.json()
        assert first_body["investigation_id"] is not None
        incident_id = first_body["incident_id"]

        correlated = await client.post(
            f"/workspaces/{workspace_id}/manual-incidents",
            headers={**operator_headers, "Idempotency-Key": f"manual-{suffix}-second"},
            json=_manual_payload(),
        )
        assert correlated.status_code == 201
        assert correlated.json()["incident_id"] == incident_id

        duplicate = await client.post(
            f"/workspaces/{workspace_id}/manual-incidents",
            headers={**operator_headers, "Idempotency-Key": f"manual-{suffix}-first"},
            json=_manual_payload(),
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["outcome"] == "duplicate"
        assert duplicate.json()["signal_id"] == first_body["signal_id"]

        overview = await client.get(f"/incidents/{incident_id}", headers=operator_headers)
        assert overview.status_code == 200
        data = overview.json()
        assert data["state"] == "open"
        assert data["severity"] == "UNCLASSIFIED"
        assert data["signal_count"] == 2
        assert len(data["signals"]) == 2
        assert all(signal["source_type"] == "manual" for signal in data["signals"])
        assert all(signal["repository_binding_id"] is None for signal in data["signals"])
        assert len(data["investigations"]) == 2
        assert {value["action"] for value in data["allowed_actions"] if value["allowed"]} >= {
            "acknowledge",
            "mitigate",
            "resolve",
            "start_investigation",
            "create_action",
        }

        viewer_overview = await client.get(f"/incidents/{incident_id}", headers=viewer_headers)
        assert viewer_overview.status_code == 200
        assert not any(value["allowed"] for value in viewer_overview.json()["allowed_actions"])
        members = await client.get(f"/incidents/{incident_id}/assignees", headers=viewer_headers)
        assert members.status_code == 200, members.text
        assert {member["user_id"] for member in members.json()} == {operator_id, viewer_id}

        acknowledged = await client.post(
            f"/incidents/{incident_id}/acknowledge",
            headers=operator_headers,
            json={
                "expected_state_version": data["state_version"],
                "reason": "Responder accepted ownership",
            },
        )
        assert acknowledged.status_code == 200
        acknowledged_data = acknowledged.json()
        assert acknowledged_data["state"] == "acknowledged"

        stale = await client.post(
            f"/incidents/{incident_id}/mitigate",
            headers=operator_headers,
            json={"expected_state_version": data["state_version"], "reason": "Stale state"},
        )
        assert stale.status_code == 409

        assigned = await client.post(
            f"/incidents/{incident_id}/assign",
            headers=operator_headers,
            json={
                "owner_id": viewer_id,
                "expected_state_version": acknowledged_data["state_version"],
                "reason": "Viewer owns the validation follow-up",
            },
        )
        assert assigned.status_code == 200
        assert assigned.json()["assigned_to"] == viewer_id

        action = await client.post(
            f"/incidents/{incident_id}/actions",
            headers=operator_headers,
            json={
                "investigation_id": first_body["investigation_id"],
                "action_type": "remediate",
                "priority": "P1",
                "title": "Repair checkout gateway handling",
                "rationale": "The run identifies the payment boundary.",
                "validation": "Run the checkout failure scenario.",
                "evidence_refs": [],
                "owner_id": viewer_id,
            },
        )
        assert action.status_code == 201
        assert action.json()["status"] == "open"
        assert action.json()["owner_id"] == viewer_id
