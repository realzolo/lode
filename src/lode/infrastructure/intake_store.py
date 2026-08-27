"""PostgreSQL persistence adapter for the intake use case."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.intake import NormalizedIncident, canonical_hash, mask_failure_payload
from lode.config import settings
from lode.crypto import encrypt_value
from lode.db.models import (
    Alert,
    DeadLetter,
    Incident,
    IngestionEvent,
    Investigation,
    InvestigationInput,
    InvestigationJob,
    InvestigationResourceGraphSnapshot,
    ResourceGraphRevision,
    SealedEvidenceValue,
    Workspace,
)
from lode.infrastructure.investigation_control_snapshots import (
    InvestigationControlSnapshotStore,
)
from lode.infrastructure.investigation_snapshots import ConnectorSnapshotStore

IntakeOutcome = Literal["accepted", "duplicate", "dead_letter", "unassigned"]


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: IntakeOutcome
    workspace_id: int | None = None
    alert_id: int | None = None
    investigation_id: int | None = None
    investigation_public_id: str | None = None
    job_id: int | None = None
    dead_letter_id: int | None = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _signature(workspace_id: int, event: str, trace_id: str | None) -> str:
    return canonical_hash({"event": event, "trace_id": trace_id, "workspace_id": workspace_id})


class PostgresIntakeStore:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve_workspace(self, topic: str, *, active_only: bool) -> Workspace | None:
        statement = select(Workspace).where(Workspace.ingestion_topic == topic)
        if active_only:
            statement = statement.where(Workspace.ingestion_state == "active")
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def existing_position(
        self, *, topic: str, partition: int, offset: int
    ) -> IntakeResult | None:
        row = (
            await self.session.execute(
                select(IngestionEvent).where(
                    IngestionEvent.topic == topic,
                    IngestionEvent.partition == partition,
                    IngestionEvent.offset == offset,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return IntakeResult(
            outcome="duplicate",
            workspace_id=row.workspace_id,
            alert_id=row.alert_row_id,
            dead_letter_id=row.dead_letter_id,
        )

    async def record_failure(
        self,
        *,
        topic: str,
        partition: int,
        offset: int,
        payload_hash: str,
        payload: Any,
        outcome: Literal["dead_letter", "unassigned"],
        reason_code: str,
        reason_detail: dict[str, Any],
        workspace_id: int | None,
    ) -> IntakeResult:
        masked_payload, categories = mask_failure_payload(payload)
        detail = dict(reason_detail)
        detail["masking_categories"] = list(categories)
        dead_letter_insert = (
            pg_insert(DeadLetter)
            .values(
                workspace_id=workspace_id,
                kind=outcome,
                topic=topic,
                partition=partition,
                offset=offset,
                payload_masked=masked_payload
                if isinstance(masked_payload, dict)
                else {"raw": masked_payload},
                reason_code=reason_code,
                reason_detail=detail,
            )
            .on_conflict_do_nothing(
                index_elements=["topic", "partition", "offset", "kind"],
                index_where=text('partition IS NOT NULL AND "offset" IS NOT NULL'),
            )
            .returning(DeadLetter.id)
        )
        dead_letter_id = (await self.session.execute(dead_letter_insert)).scalar_one_or_none()
        if dead_letter_id is None:
            dead_letter_id = (
                await self.session.execute(
                    select(DeadLetter.id).where(
                        DeadLetter.topic == topic,
                        DeadLetter.partition == partition,
                        DeadLetter.offset == offset,
                        DeadLetter.kind == outcome,
                    )
                )
            ).scalar_one()

        event_id = (
            await self.session.execute(
                pg_insert(IngestionEvent)
                .values(
                    workspace_id=workspace_id,
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    payload_hash=payload_hash,
                    outcome=outcome,
                    dead_letter_id=dead_letter_id,
                )
                .on_conflict_do_nothing(constraint="uq_ingestion_event_position")
                .returning(IngestionEvent.id)
            )
        ).scalar_one_or_none()
        await self.session.commit()
        if event_id is None:
            return IntakeResult(outcome="duplicate", workspace_id=workspace_id)
        return IntakeResult(
            outcome=outcome,
            workspace_id=workspace_id,
            dead_letter_id=dead_letter_id,
        )

    async def persist_kafka(
        self,
        *,
        workspace_id: int,
        topic: str,
        partition: int,
        offset: int,
        payload_hash: str,
        incident: NormalizedIncident,
        replay_event_id: int | None = None,
        replay_dead_letter_id: int | None = None,
    ) -> IntakeResult:
        if (
            incident.alert_id is None
            or incident.trace_id is None
            or incident.source_revision is None
        ):
            raise ValueError("Kafka normalization requires alert, trace, and source revision")

        if replay_event_id is None:
            event_id = (
                await self.session.execute(
                    pg_insert(IngestionEvent)
                    .values(
                        workspace_id=workspace_id,
                        topic=topic,
                        partition=partition,
                        offset=offset,
                        payload_hash=payload_hash,
                        alert_id=incident.alert_id,
                        outcome="accepted",
                    )
                    .on_conflict_do_nothing(constraint="uq_ingestion_event_position")
                    .returning(IngestionEvent.id)
                )
            ).scalar_one_or_none()
            if event_id is None:
                await self.session.rollback()
                return IntakeResult(outcome="duplicate", workspace_id=workspace_id)
        else:
            event_id = replay_event_id
            await self.session.execute(
                update(IngestionEvent)
                .where(
                    IngestionEvent.id == event_id,
                    IngestionEvent.topic == topic,
                    IngestionEvent.partition == partition,
                    IngestionEvent.offset == offset,
                )
                .values(
                    workspace_id=workspace_id,
                    payload_hash=payload_hash,
                    alert_id=incident.alert_id,
                    outcome="accepted",
                )
            )
            if replay_dead_letter_id is not None:
                await self.session.execute(
                    update(DeadLetter)
                    .where(DeadLetter.id == replay_dead_letter_id, DeadLetter.replayed.is_(False))
                    .values(replayed=True, replayed_at=datetime.now(UTC))
                )

        trace_hash = _sha256(incident.trace_id)
        trace_ciphertext = encrypt_value(incident.trace_id)
        alert_row_id = (
            await self.session.execute(
                pg_insert(Alert)
                .values(
                    workspace_id=workspace_id,
                    alert_id=incident.alert_id,
                    occurred_at=incident.occurred_at,
                    severity=incident.severity,
                    event=incident.event,
                    trace_id_ciphertext=trace_ciphertext,
                    trace_id_hash=trace_hash,
                    source_revision=incident.source_revision,
                    error=incident.error_masked,
                    raw_payload_masked=incident.raw_payload_masked,
                )
                .on_conflict_do_nothing(constraint="uq_alert_workspace_id")
                .returning(Alert.id)
            )
        ).scalar_one_or_none()
        if alert_row_id is None:
            alert_row_id = (
                await self.session.execute(
                    select(Alert.id).where(
                        Alert.workspace_id == workspace_id,
                        Alert.alert_id == incident.alert_id,
                    )
                )
            ).scalar_one()
            await self.session.execute(
                update(IngestionEvent)
                .where(IngestionEvent.id == event_id)
                .values(outcome="duplicate", alert_row_id=alert_row_id)
            )
            await self.session.commit()
            return IntakeResult(
                outcome="duplicate", workspace_id=workspace_id, alert_id=alert_row_id
            )

        signature_hash = _signature(workspace_id, incident.event, incident.trace_id)
        incident_id = (
            await self.session.execute(
                pg_insert(Incident)
                .values(
                    workspace_id=workspace_id,
                    signature_hash=signature_hash,
                    event=incident.event,
                    trace_id_hash=trace_hash,
                    first_occurred_at=incident.occurred_at,
                    last_occurred_at=incident.occurred_at,
                    latest_alert_id=alert_row_id,
                )
                .on_conflict_do_nothing(
                    index_elements=["workspace_id", "signature_hash"],
                    index_where=text("state = 'active'"),
                )
                .returning(Incident.id)
            )
        ).scalar_one_or_none()
        if incident_id is None:
            existing_incident = (
                await self.session.execute(
                    select(Incident)
                    .where(
                        Incident.workspace_id == workspace_id,
                        Incident.signature_hash == signature_hash,
                        Incident.state == "active",
                    )
                    .with_for_update()
                )
            ).scalar_one()
            existing_incident.last_occurred_at = max(
                existing_incident.last_occurred_at, incident.occurred_at
            )
            existing_incident.latest_alert_id = alert_row_id
            existing_incident.occurrence_count += 1
            await self.session.execute(
                update(IngestionEvent)
                .where(IngestionEvent.id == event_id)
                .values(outcome="duplicate", alert_row_id=alert_row_id)
            )
            await self.session.commit()
            return IntakeResult(
                outcome="duplicate", workspace_id=workspace_id, alert_id=alert_row_id
            )

        result = await self._create_investigation(
            workspace_id=workspace_id,
            incident=incident,
            alert_row_id=alert_row_id,
            incident_id=incident_id,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
            created_by=None,
        )
        await self.session.execute(
            update(IngestionEvent)
            .where(IngestionEvent.id == event_id)
            .values(alert_row_id=alert_row_id)
        )
        await self.session.commit()
        return result

    async def persist_manual(
        self,
        *,
        workspace_id: int,
        incident: NormalizedIncident,
        created_by: int,
        retry_of_id: int | None = None,
    ) -> IntakeResult:
        trace_ciphertext = (
            encrypt_value(incident.trace_id) if incident.trace_id is not None else None
        )
        trace_hash = _sha256(incident.trace_id) if incident.trace_id is not None else None
        result = await self._create_investigation(
            workspace_id=workspace_id,
            incident=incident,
            alert_row_id=None,
            incident_id=None,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
            created_by=created_by,
            retry_of_id=retry_of_id,
        )
        await self.session.commit()
        return result

    async def _create_investigation(
        self,
        *,
        workspace_id: int,
        incident: NormalizedIncident,
        alert_row_id: int | None,
        incident_id: int | None,
        trace_ciphertext: str | None,
        trace_hash: str | None,
        created_by: int | None,
        retry_of_id: int | None = None,
    ) -> IntakeResult:
        signature_hash = _signature(workspace_id, incident.event, incident.trace_id)
        investigation = Investigation(
            public_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            alert_id=alert_row_id,
            incident_id=incident_id,
            retry_of_id=retry_of_id,
            trigger_signature_hash=signature_hash,
            output_language="en",
            window_started_at=incident.occurred_at
            - timedelta(seconds=settings.investigation_window_before_seconds),
            window_finished_at=incident.occurred_at
            + timedelta(seconds=settings.investigation_window_after_seconds),
            execution_budget={
                "max_evidence_steps": settings.investigation_max_evidence_steps,
                "max_model_calls": settings.investigation_max_model_calls,
                "max_native_reads": settings.investigation_max_native_reads,
                "max_output_bytes": settings.investigation_max_output_bytes,
                "max_cost": settings.investigation_max_cost,
                "timeout_seconds": settings.investigation_timeout_seconds,
                "max_parallel_operations": 4,
            },
            engine_version="lode",
        )
        self.session.add(investigation)
        await self.session.flush()

        trace_value_ref = "incident.trace_id" if incident.trace_id is not None else None
        self.session.add(
            InvestigationInput(
                investigation_id=investigation.id,
                source_type=incident.source_type,
                event=incident.event,
                severity=incident.severity,
                occurred_at=incident.occurred_at,
                trace_value_ref=trace_value_ref,
                source_revision=incident.source_revision,
                error=incident.error_masked,
                raw_payload_masked=incident.raw_payload_masked,
                attachments_masked=list(incident.attachments_masked),
                created_by=created_by,
            )
        )
        if trace_value_ref is not None and trace_ciphertext is not None and trace_hash is not None:
            self.session.add(
                SealedEvidenceValue(
                    workspace_id=workspace_id,
                    investigation_id=investigation.id,
                    value_ref=trace_value_ref,
                    value_ciphertext=trace_ciphertext,
                    value_hash=trace_hash,
                    value_type="string",
                    data_class="opaque_correlation_value",
                    envelope_key_version="data-encryption-key.v1",
                )
            )
        graph_revision = (
            await self.session.execute(
                select(ResourceGraphRevision)
                .where(ResourceGraphRevision.workspace_id == workspace_id)
                .order_by(ResourceGraphRevision.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        self.session.add(
            InvestigationResourceGraphSnapshot(
                investigation_id=investigation.id,
                resource_graph_revision_id=None if graph_revision is None else graph_revision.id,
                graph_revision=None if graph_revision is None else graph_revision.revision,
                snapshot_hash=canonical_hash(
                    {
                        "resource_graph_revision_id": None
                        if graph_revision is None
                        else graph_revision.id,
                        "graph_revision": None
                        if graph_revision is None
                        else graph_revision.revision,
                        "input_hash": None if graph_revision is None else graph_revision.input_hash,
                    }
                ),
            )
        )
        await InvestigationControlSnapshotStore(self.session).freeze(
            investigation_id=investigation.id,
            workspace_id=workspace_id,
            incident_source_revision=incident.source_revision,
        )
        await ConnectorSnapshotStore.freeze_in_session(self.session, investigation.id)
        job = InvestigationJob(investigation_id=investigation.id)
        self.session.add(job)
        await self.session.flush()
        return IntakeResult(
            outcome="accepted",
            workspace_id=workspace_id,
            alert_id=alert_row_id,
            investigation_id=investigation.id,
            investigation_public_id=investigation.public_id,
            job_id=job.id,
        )
