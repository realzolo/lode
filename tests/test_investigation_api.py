"""Database-backed investigation event replay and lifecycle API tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.config import settings
from lode.application.investigation_policy import investigation_policy_columns
from lode.db.models import (
    Investigation,
    InvestigationDecision,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationStep,
    InvestigationPolicyRevision,
    User,
    Workspace,
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
            email=f"stream-{suffix}@lode.local",
            name="Stream Admin",
            password_hash=hash_password("correct-horse-battery"),
            role="admin",
            status="active",
        )
        workspace = Workspace(
            name=f"stream-{suffix}",
            ingestion_topic=f"stream-{suffix}",
        )
        session.add_all([user, workspace])
        await session.flush()
        policy = InvestigationPolicyRevision(
            workspace_id=workspace.id,
            profile="balanced",
            **investigation_policy_columns("balanced"),
            revision=1,
            created_by=user.id,
        )
        session.add(policy)
        await session.flush()
        workspace.investigation_policy_revision_id = policy.id
        session.add(
            WorkspacePermission(
                user_id=user.id,
                workspace_id=workspace.id,
                permission="admin",
            )
        )
        investigation = Investigation(
            public_id=str(uuid.uuid4()),
            workspace_id=workspace.id,
            investigation_policy_revision_id=policy.id,
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
        public_id = investigation.public_id
        user_id = user.id

    token = create_token(user_id, settings.jwt_signing_key, 3600)
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        replay = await client.get(f"/investigations/{public_id}/events", headers=headers)
        assert replay.status_code == 200
        assert [item["sequence"] for item in replay.json()] == [1]

        stream = await client.get(
            f"/investigations/{public_id}/stream",
            headers={**headers, "Last-Event-ID": "1"},
        )
        assert stream.status_code == 200
        assert "event: operation.finished" not in stream.text
        assert "event: investigation.finished" in stream.text
        assert '"status":"completed"' in stream.text

        archived = await client.post(f"/investigations/{public_id}/archive", headers=headers)
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

    async with AsyncSessionLocal() as session:
        persisted = await session.scalar(
            select(Investigation).where(Investigation.public_id == public_id)
        )
        assert persisted is not None
        assert persisted.archived_by == user_id
        assert persisted.archived_at is not None
