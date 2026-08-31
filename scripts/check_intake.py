#!/usr/bin/env python3
"""Exercise Kafka/manual intake against an upgraded PostgreSQL database."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
from current_git_fixture import (
    FIXTURE_ADAPTER_ID,
    FIXTURE_ENDPOINT_HASH,
    ensure_repository_access,
)
from pydantic import ValidationError
from sqlalchemy import func, select

from lode.api.main import app
from lode.config import settings
from lode.consumer.main import KafkaIntakeHandler
from lode.crypto import decrypt_value
from lode.db.models import (
    DeadLetter,
    EvidenceArtifact,
    EvidenceCollection,
    GitRepository,
    Incident,
    IncidentCorrelationDecision,
    IncidentSignal,
    IncidentSignalLink,
    IngestionEvent,
    Investigation,
    SealedEvidenceValue,
    User,
    Workspace,
    WorkspaceArchitectureContextRevision,
    WorkspacePermission,
    WorkspaceRepositoryBinding,
)
from lode.db.session import AsyncSessionLocal
from lode.development.isolated_database import require_isolated_database
from lode.security import create_token


def _payload(*, alert_id: str, trace_id: str, event: str = "payment.order_create.failed") -> dict:
    return {
        "schema_version": "incident.alert.v1",
        "alert_id": alert_id,
        "occurred_at": "2026-08-26T09:30:00.000Z",
        "severity": "CRITICAL",
        "event": event,
        "trace_id": trace_id,
        "source_revision": "a" * 40,
        "error": {
            "type": "GatewayError",
            "message": "Payment creation failed",
            "stack": "complete stack",
            "cause": None,
        },
    }


async def main() -> None:
    require_isolated_database("intake check")
    trace_id = '  引号\'" {job=~".*"} | json  '
    async with AsyncSessionLocal() as session:
        user = User(
            username="intake-check",
            display_name="Intake Check",
            password_hash="checker",
            status="active",
        )
        workspace = Workspace(
            name="Intake behavior",
            ingestion_topic="incident.intake.behavior.v1",
            ingestion_state="active",
            ingestion_version=1,
            ingestion_start_position="earliest",
            ingestion_activation_kind="start",
            ingestion_started_at=datetime.now(UTC),
        )
        session.add_all([user, workspace])
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
        repository = GitRepository(
            adapter_id=FIXTURE_ADAPTER_ID,
            endpoint_identity_hash=FIXTURE_ENDPOINT_HASH,
            external_repository_id="intake-check",
            name="intake-check",
            full_name="example/intake-check",
            repo_url="https://example.invalid/intake-check.git",
            web_url="https://example.invalid/intake-check",
            repo_type="other",
            default_branch="main",
            visibility="private",
        )
        session.add(repository)
        await session.flush()
        account_connection_id = await ensure_repository_access(session, workspace.id, repository)
        session.add(
            WorkspaceRepositoryBinding(
                workspace_id=workspace.id,
                repository_id=repository.id,
                account_connection_id=account_connection_id,
                analysis_mode="code",
                is_alert_source=True,
            )
        )
        session.add(
            WorkspacePermission(
                workspace_id=workspace.id,
                user_id=user.id,
                permission="operator",
            )
        )
        await session.commit()
        await session.refresh(user)
        await session.refresh(workspace)
        user_id = user.id
        workspace_id = workspace.id

    handler = KafkaIntakeHandler()
    first_payload = _payload(alert_id="alert-1", trace_id=trace_id)
    first_raw = json.dumps(first_payload, ensure_ascii=False).encode("utf-8")
    first = await handler.handle(
        topic="incident.intake.behavior.v1", partition=0, offset=0, raw=first_raw
    )
    redelivery = await handler.handle(
        topic="incident.intake.behavior.v1", partition=0, offset=0, raw=first_raw
    )
    producer_duplicate = await handler.handle(
        topic="incident.intake.behavior.v1", partition=0, offset=1, raw=first_raw
    )
    incident_duplicate = await handler.handle(
        topic="incident.intake.behavior.v1",
        partition=0,
        offset=2,
        raw=json.dumps(_payload(alert_id="alert-2", trace_id=trace_id), ensure_ascii=False).encode(
            "utf-8"
        ),
    )

    invalid = dict(first_payload)
    invalid["service" + "_name"] = "removed-field"
    dead_letter = await handler.handle(
        topic="incident.intake.behavior.v1",
        partition=0,
        offset=3,
        raw=json.dumps(invalid, ensure_ascii=False).encode("utf-8"),
    )
    extended_payload = _payload(
        alert_id="unsupported-recovery-event",
        trace_id="",
        event="payment.unknown.recovered",
    )
    extended_payload["event_kind"] = "recovered"
    unsupported_extension = await handler.handle(
        topic="incident.intake.behavior.v1",
        partition=0,
        offset=4,
        raw=json.dumps(extended_payload, ensure_ascii=False).encode("utf-8"),
    )
    unassigned = await handler.handle(
        topic="incident.unassigned.v1",
        partition=0,
        offset=0,
        raw=first_raw,
    )

    same_offset_raw = json.dumps(
        _payload(
            alert_id="alert-concurrent-offset",
            trace_id="concurrent-offset",
            event="payment.concurrent_offset.failed",
        )
    ).encode("utf-8")
    same_offset_results = await asyncio.gather(
        handler.handle(
            topic="incident.intake.behavior.v1", partition=0, offset=10, raw=same_offset_raw
        ),
        handler.handle(
            topic="incident.intake.behavior.v1", partition=0, offset=10, raw=same_offset_raw
        ),
    )
    producer_race_raw = json.dumps(
        _payload(
            alert_id="alert-concurrent-producer",
            trace_id="concurrent-producer",
            event="payment.concurrent_producer.failed",
        )
    ).encode("utf-8")
    producer_race_results = await asyncio.gather(
        handler.handle(
            topic="incident.intake.behavior.v1", partition=0, offset=11, raw=producer_race_raw
        ),
        handler.handle(
            topic="incident.intake.behavior.v1", partition=0, offset=12, raw=producer_race_raw
        ),
    )
    if sorted(result.outcome for result in same_offset_results) != ["accepted", "duplicate"]:
        raise RuntimeError("same-offset race did not produce one accepted record")
    if sorted(result.outcome for result in producer_race_results) != ["accepted", "duplicate"]:
        raise RuntimeError("alert-ID race did not produce one accepted signal")

    try:
        await handler.replay(
            dead_letter_id=dead_letter.dead_letter_id or 0,
            raw=json.dumps(invalid).encode("utf-8"),
        )
    except ValidationError:
        pass
    else:
        raise RuntimeError("DLQ replay accepted a removed Kafka field")
    async with AsyncSessionLocal() as session:
        rejected_replay = await session.get(DeadLetter, dead_letter.dead_letter_id)
        if rejected_replay is None or rejected_replay.replayed:
            raise RuntimeError("rejected replay changed the dead-letter state")

    replay_payload = _payload(
        alert_id="alert-replayed",
        trace_id="",
        event="payment.replay.failed",
    )
    replay_result = await handler.replay(
        dead_letter_id=dead_letter.dead_letter_id or 0,
        raw=json.dumps(replay_payload).encode("utf-8"),
    )
    if replay_result.outcome != "accepted":
        raise RuntimeError(f"valid DLQ replay was not accepted: {replay_result.outcome}")

    manual_payload = {
        "schema_version": "manual-incident.v1",
        "summary": "Manual runtime failure",
        "error_text": "RuntimeError: manual input\n  at worker.py:7",
    }
    headers = {"authorization": f"Bearer {create_token(user_id, settings.jwt_signing_key)}"}
    manual_headers = {**headers, "Idempotency-Key": "manual-intake-check-request"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://intake.test"
    ) as client:
        invalid_manual = await client.post(
            f"/workspaces/{workspace_id}/manual-incidents",
            headers={**headers, "Idempotency-Key": "manual-invalid-intake-request"},
            json={**manual_payload, "environment": "production"},
        )
        if invalid_manual.status_code != 422:
            raise RuntimeError("manual endpoint accepted a removed environment field")
        manual_response = await client.post(
            f"/workspaces/{workspace_id}/manual-incidents",
            headers=manual_headers,
            json=manual_payload,
        )
        if manual_response.status_code != 201:
            raise RuntimeError(
                f"manual endpoint failed: {manual_response.status_code} {manual_response.text}"
            )
        if manual_response.json()["investigation_id"] is None:
            raise RuntimeError("manual endpoint did not create an investigation run")
        manual_duplicate = await client.post(
            f"/workspaces/{workspace_id}/manual-incidents",
            headers=manual_headers,
            json=manual_payload,
        )
        if manual_duplicate.status_code != 201 or manual_duplicate.json()["outcome"] != "duplicate":
            raise RuntimeError("manual Idempotency-Key did not prevent duplicate intake")

    outcomes = [
        first.outcome,
        redelivery.outcome,
        producer_duplicate.outcome,
        incident_duplicate.outcome,
        dead_letter.outcome,
        unsupported_extension.outcome,
        unassigned.outcome,
        "accepted",
    ]
    expected = [
        "accepted",
        "duplicate",
        "duplicate",
        "correlated",
        "dead_letter",
        "dead_letter",
        "unassigned",
        "accepted",
    ]
    if outcomes != expected:
        raise RuntimeError(f"unexpected intake outcomes: {outcomes}")

    async with AsyncSessionLocal() as session:
        signal = (
            await session.execute(
                select(IncidentSignal).where(IncidentSignal.source_event_id == "alert-1")
            )
        ).scalar_one()
        sealed = (
            await session.execute(
                select(SealedEvidenceValue).where(
                    SealedEvidenceValue.investigation_id == first.investigation_id,
                    SealedEvidenceValue.value_ref == "incident.trace_id",
                )
            )
        ).scalar_one()
        replayed_dead_letter = await session.get(DeadLetter, dead_letter.dead_letter_id)
        durable_failures = (
            (await session.execute(select(DeadLetter).order_by(DeadLetter.id))).scalars().all()
        )
        link = await session.get(IncidentSignalLink, signal.id)
        if link is None:
            raise RuntimeError("Kafka signal has no current incident association")
        incident = await session.get(Incident, link.incident_id)
        if incident is None:
            raise RuntimeError("Kafka signal association references no incident")
        manual_signal = (
            await session.execute(
                select(IncidentSignal).where(
                    IncidentSignal.workspace_id == workspace_id,
                    IncidentSignal.source_type == "manual",
                )
            )
        ).scalar_one()
        manual_decision = (
            await session.execute(
                select(IncidentCorrelationDecision).where(
                    IncidentCorrelationDecision.signal_id == manual_signal.id
                )
            )
        ).scalar_one()
        incident_input_artifact = (
            await session.execute(
                select(EvidenceArtifact).where(
                    EvidenceArtifact.investigation_id == first.investigation_id,
                    EvidenceArtifact.artifact_kind == "incident_input",
                )
            )
        ).scalar_one()
        incident_input_collection = await session.get(
            EvidenceCollection, incident_input_artifact.collection_id
        )
        counts = {
            "signals": await session.scalar(select(func.count(IncidentSignal.id))),
            "dead_letters": await session.scalar(select(func.count(DeadLetter.id))),
            "ingestion_events": await session.scalar(select(func.count(IngestionEvent.id))),
            "investigations": await session.scalar(select(func.count(Investigation.id))),
        }

    if decrypt_value(signal.trace_id_ciphertext) != trace_id:
        raise RuntimeError("Signal trace ciphertext did not preserve the original value")
    if decrypt_value(sealed.value_ciphertext) != trace_id:
        raise RuntimeError("ValueRef vault did not preserve the original value")
    if signal.raw_payload_masked["trace_id"] != "<VALUE_REF:incident.trace_id>":
        raise RuntimeError("masked signal payload exposed its trace value")
    if {"component", "environment", "dedup_key", "event_kind"}.intersection(
        signal.raw_payload_masked
    ):
        raise RuntimeError("Kafka v1 signal persisted fields outside the wire contract")
    if incident.signal_count != 2:
        raise RuntimeError("active incident deduplication did not count the second alert")
    if (
        manual_signal.severity != "UNCLASSIFIED"
        or manual_signal.repository_binding_id is not None
        or manual_signal.trace_id_ciphertext is not None
        or manual_decision.outcome != "new"
    ):
        raise RuntimeError("minimal manual intake inferred a source, trace, severity, or association")
    if incident_input_collection is None:
        raise RuntimeError("canonical incident input collection is missing")
    if (
        incident_input_collection.collection_kind != "input"
        or incident_input_collection.status != "succeeded"
        or incident_input_collection.artifact_count != 1
    ):
        raise RuntimeError("canonical incident input collection is invalid")
    input_content = incident_input_artifact.content_masked
    if (
        input_content["title"] != "payment.order_create.failed"
        or input_content["error"]["message"] != "Payment creation failed"
        or input_content["trace_value_ref"] != "incident.trace_id"
        or input_content["raw_payload"]["trace_id"] != "<VALUE_REF:incident.trace_id>"
    ):
        raise RuntimeError("canonical incident input evidence is incomplete or unmasked")
    if replayed_dead_letter is None or not replayed_dead_letter.replayed:
        raise RuntimeError("valid replay did not mark its dead letter")
    if any(
        row.payload_masked.get("trace_id") != "<VALUE_REF:incident.trace_id>"
        for row in durable_failures
        if "trace_id" in row.payload_masked
    ):
        raise RuntimeError("durable failure payload exposed an opaque trace")
    if counts != {
        "signals": 6,
        "dead_letters": 3,
        "ingestion_events": 9,
        "investigations": 5,
    }:
        raise RuntimeError(f"unexpected persisted counts: {counts}")

    print(
        json.dumps(
            {
                "counts": counts,
                "outcomes": outcomes,
                "replay_outcome": replay_result.outcome,
                "trace_round_trip": True,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
