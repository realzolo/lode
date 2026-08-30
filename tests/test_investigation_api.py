"""Database-backed investigation event replay and lifecycle API tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.config import settings
from lode.db.models import (
    Investigation,
    InvestigationDecision,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationStep,
    User,
    Workspace,
    WorkspaceArchitectureContextRevision,
    WorkspacePermission,
)
from lode.db.session import AsyncSessionLocal
from lode.security import create_token, hash_password


@pytest.mark.asyncio
async def test_event_stream_replays_cursor_and_archive_is_durable() -> None:
    suffix = uuid.uuid4().hex
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        user = User(
            username=f"stream-{suffix[:12]}",
            display_name="Stream User",
            password_hash=hash_password("correct-horse-battery"),
            status="active",
            must_change_password=False,
        )
        outsider = User(
            username=f"outsider-{suffix[:10]}",
            display_name="Outside Viewer",
            password_hash=hash_password("correct-horse-battery"),
            status="active",
            must_change_password=False,
        )
        workspace = Workspace(
            name=f"stream-{suffix}",
            ingestion_topic=f"stream-{suffix}",
        )
        session.add_all([user, outsider, workspace])
        await session.flush()
        architecture_context = WorkspaceArchitectureContextRevision(
            workspace_id=workspace.id,
            entries=[],
            revision=1,
            created_by=user.id,
        )
        session.add(architecture_context)
        await session.flush()
        workspace.architecture_context_revision_id = architecture_context.id
        session.add(
            WorkspacePermission(
                user_id=user.id,
                workspace_id=workspace.id,
                permission="operator",
            )
        )
        investigation = Investigation(
            workspace_id=workspace.id,
            trigger_signature_hash="a" * 64,
            status="running",
            result_state="pending",
            output_language="en",
            window_started_at=now - timedelta(minutes=5),
            window_finished_at=now + timedelta(minutes=5),
            execution_budget={},
            engine_version="lode",
            event_cursor=1,
            started_at=now,
        )
        session.add(investigation)
        await session.flush()
        step = InvestigationStep(
            investigation_id=investigation.id,
            ordinal=1,
            objective="Collect evidence",
            status="succeeded",
            hypothesis_snapshot={},
            input_evidence_refs=[],
            output_evidence_refs=[],
            started_at=now,
            finished_at=now,
        )
        session.add(step)
        await session.flush()
        decision = InvestigationDecision(
            investigation_id=investigation.id,
            step_id=step.id,
            ordinal=1,
            decision="continue",
            hypotheses=[],
            operation_plan=[{}],
            policy_outcome="allow",
            policy_decisions=[],
            selected_operation_count=1,
            decision_hash="b" * 64,
        )
        session.add(decision)
        await session.flush()
        operation = InvestigationOperation(
            investigation_id=investigation.id,
            step_id=step.id,
            decision_id=decision.id,
            ordinal=1,
            wave_ordinal=1,
            action_id="collect",
            operation_kind="snapshot",
            purpose="Collect immutable evidence",
            expected_evidence="snapshot",
            evidence_anchors=["incident"],
            selection_reason="required",
            stop_condition="captured",
            input_masked={},
            fingerprint="c" * 64,
            status="succeeded",
            result_masked={},
            started_at=now,
            finished_at=now,
        )
        session.add(operation)
        await session.flush()
        session.add(
            InvestigationOperationEvent(
                investigation_id=investigation.id,
                operation_id=operation.id,
                sequence=1,
                event_name="operation.finished",
                message="Snapshot captured",
                detail_masked={},
                evidence_refs=[],
                occurred_at=now,
            )
        )
        investigation.status = "completed"
        investigation.result_state = "insufficient"
        investigation.finished_at = now
        await session.commit()
        investigation_id = investigation.id
        user_id = user.id
        outsider_id = outsider.id

    token = create_token(user_id, settings.jwt_signing_key, 3600)
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        replay = await client.get(f"/investigations/{investigation_id}/events", headers=headers)
        assert replay.status_code == 200
        assert [item["sequence"] for item in replay.json()] == [1]

        graph_response = await client.get(
            f"/investigations/{investigation_id}/execution-graph", headers=headers
        )
        assert graph_response.status_code == 200
        graph = graph_response.json()
        assert graph["schema_version"] == "investigation-execution-graph.v1"
        assert graph["phase"] == "completed"
        assert [node["id"] for node in graph["nodes"]] == [
            f"input:{investigation_id}",
            f"decision:{decision.id}",
            f"operation:{operation.id}",
        ]
        assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
            (f"input:{investigation_id}", f"decision:{decision.id}"),
            (f"decision:{decision.id}", f"operation:{operation.id}"),
        ]

        node_response = await client.get(
            f"/investigations/{investigation_id}/execution-graph/nodes/operation:{operation.id}",
            headers=headers,
        )
        assert node_response.status_code == 200
        node_detail = node_response.json()
        assert node_detail["overview"]["purpose"] == "Collect immutable evidence"
        assert node_detail["events"][0]["message"] == "Snapshot captured"
        assert "token_hash" not in node_response.text
        assert "ciphertext" not in node_response.text

        missing_artifact = await client.get(
            f"/investigations/{investigation_id}/execution-graph/nodes/operation:{operation.id}"
            "/artifacts/1",
            headers=headers,
        )
        assert missing_artifact.status_code == 404

        stream = await client.get(
            f"/investigations/{investigation_id}/stream",
            headers={**headers, "Last-Event-ID": "1"},
        )
        assert stream.status_code == 200
        assert "event: operation.finished" not in stream.text
        assert "event: investigation.finished" in stream.text
        assert '"status":"completed"' in stream.text

        outside_response = await client.get(
            f"/investigations/{investigation_id}/execution-graph",
            headers={
                "Authorization": "Bearer "
                + create_token(outsider_id, settings.jwt_signing_key, 3600)
            },
        )
        assert outside_response.status_code == 403

    async with AsyncSessionLocal() as session:
        admin = await session.scalar(select(User).where(User.is_system_admin))
        assert admin is not None
        admin.must_change_password = False
        await session.commit()
        admin_id = admin.id
    admin_headers = {"Authorization": "Bearer " + create_token(admin_id, settings.jwt_signing_key, 3600)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        archived = await client.post(f"/admin/investigations/{investigation_id}/archive", headers=admin_headers)
        assert archived.status_code == 200

    async with AsyncSessionLocal() as session:
        persisted = await session.get(Investigation, investigation_id)
        assert persisted is not None
        assert persisted.archived_by == admin_id
        assert persisted.archived_at is not None
