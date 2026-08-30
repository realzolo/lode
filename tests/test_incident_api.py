"""Incident-first operational API tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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


def _manual_payload(workspace_id: int) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "dedup_key": "checkout.payment.failure",
        "event_kind": "firing",
        "occurred_at": datetime.now(UTC).isoformat(),
        "severity": "CRITICAL",
        "event": "checkout.payment.failed",
        "component": "checkout-api",
        "environment": "production",
        "trace_id": "incident-trace",
        "source_revision": None,
        "error": {
            "type": "GatewayError",
            "message": "payment gateway failed",
            "stack": "gateway.py:1",
            "cause": None,
        },
        "attachments": [],
    }


@pytest.mark.asyncio
async def test_incident_api_correlates_occurrences_and_enforces_server_capabilities() -> None:
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
            "/incidents", headers=operator_headers, json=_manual_payload(workspace_id)
        )
        assert first.status_code == 201
        first_body = first.json()
        assert first_body["investigation_id"] is not None
        incident_id = first_body["incident_id"]

        correlated = await client.post(
            "/incidents", headers=operator_headers, json=_manual_payload(workspace_id)
        )
        assert correlated.status_code == 201
        assert correlated.json()["incident_id"] == incident_id

        overview = await client.get(f"/incidents/{incident_id}", headers=operator_headers)
        assert overview.status_code == 200
        data = overview.json()
        assert data["state"] == "open"
        assert data["occurrence_count"] == 2
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
            },
        )
        assert action.status_code == 201
        assert action.json()["status"] == "proposed"
