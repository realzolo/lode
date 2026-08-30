"""PostgreSQL persistence adapter for the intake use case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.intake import NormalizedIncident, canonical_hash, mask_failure_payload
from lode.application.investigation_limits import (
    INVESTIGATION_HARD_LIMITS,
    investigation_execution_budget,
)
from lode.crypto import decrypt_value, encrypt_value
from lode.db.models import (
    DeadLetter,
    EvidenceArtifact,
    EvidenceCollection,
    GitRepository,
    Incident,
    IncidentEvent,
    IncidentOccurrence,
    IngestionEvent,
    Investigation,
    InvestigationInput,
    InvestigationJob,
    InvestigationResourceGraphSnapshot,
    PlatformSettings,
    RepositoryAnalysisJob,
    ResourceGraphRevision,
    SealedEvidenceValue,
    Workspace,
    WorkspaceRepositoryBinding,
)
from lode.infrastructure.investigation_control_snapshots import (
    InvestigationControlSnapshotStore,
)
from lode.infrastructure.investigation_snapshots import ConnectorSnapshotStore

IntakeOutcome = Literal["accepted", "correlated", "duplicate", "dead_letter", "unassigned"]


class IncidentCorrelationError(ValueError):
    """A valid intake event cannot be attached to an operational incident."""


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: IntakeOutcome
    workspace_id: int | None = None
    occurrence_id: int | None = None
    incident_id: int | None = None
    investigation_id: int | None = None
    job_id: int | None = None
    dead_letter_id: int | None = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _trigger_signature(workspace_id: int, incident: NormalizedIncident) -> str:
    return canonical_hash(
        {
            "workspace_id": workspace_id,
            "dedup_key": incident.dedup_key,
            "event_kind": incident.event_kind,
            "occurred_at": incident.occurred_at.isoformat(),
        }
    )


def _incident_evidence_content(
    incident: NormalizedIncident, trace_value_ref: str | None
) -> dict[str, Any]:
    return {
        "source_type": incident.source_type,
        "source_event_id": incident.source_event_id,
        "dedup_key": incident.dedup_key,
        "event_kind": incident.event_kind,
        "event": incident.event,
        "component": incident.component,
        "environment": incident.environment,
        "severity": incident.severity,
        "occurred_at": incident.occurred_at.isoformat(),
        "trace_value_ref": trace_value_ref,
        "source_revision": incident.source_revision,
        "error": incident.error_masked,
        "raw_payload": incident.raw_payload_masked,
        "attachments": list(incident.attachments_masked),
    }


async def _repository_analysis_current(session: AsyncSession, workspace_id: int) -> bool | None:
    latest = (
        await session.execute(
            select(RepositoryAnalysisJob)
            .where(
                RepositoryAnalysisJob.workspace_id == workspace_id,
                RepositoryAnalysisJob.state == "succeeded",
            )
            .order_by(RepositoryAnalysisJob.created_at.desc(), RepositoryAnalysisJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return None
    rows = (
        await session.execute(
            select(WorkspaceRepositoryBinding, GitRepository)
            .join(GitRepository, GitRepository.id == WorkspaceRepositoryBinding.repository_id)
            .where(
                WorkspaceRepositoryBinding.workspace_id == workspace_id,
                WorkspaceRepositoryBinding.state == "active",
            )
            .order_by(WorkspaceRepositoryBinding.id)
        )
    ).all()
    snapshot = [
        {
            "binding_id": binding.id,
            "configuration_revision": binding.descriptor_revision,
            "repository_id": repository.id,
            "account_connection_id": binding.account_connection_id,
            "analysis_mode": binding.analysis_mode,
            "is_alert_source": binding.is_alert_source,
            "branch_mode": binding.branch_mode,
            "effective_branch": binding.branch_name
            if binding.branch_mode == "branch"
            else repository.default_branch,
        }
        for binding, repository in rows
    ]
    return bool(snapshot) and latest.input_hash == canonical_hash({"repository_bindings": snapshot})


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
            occurrence_id=row.occurrence_id,
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
        if incident.source_event_id is None:
            raise ValueError("Kafka normalization requires source_event_id")

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
                        source_event_id=incident.source_event_id,
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
                    source_event_id=incident.source_event_id,
                    outcome="accepted",
                )
            )
            if replay_dead_letter_id is not None:
                await self.session.execute(
                    update(DeadLetter)
                    .where(DeadLetter.id == replay_dead_letter_id, DeadLetter.replayed.is_(False))
                    .values(replayed=True, replayed_at=datetime.now(UTC))
                )

        trace_ciphertext = (
            encrypt_value(incident.trace_id) if incident.trace_id is not None else None
        )
        trace_hash = _sha256(incident.trace_id) if incident.trace_id is not None else None
        incident_row, occurrence, created, duplicate = await self._record_occurrence(
            workspace_id=workspace_id,
            incident=incident,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
        )
        if duplicate:
            await self.session.execute(
                update(IngestionEvent)
                .where(IngestionEvent.id == event_id)
                .values(outcome="duplicate", occurrence_id=occurrence.id)
            )
            await self.session.commit()
            return IntakeResult(
                outcome="duplicate",
                workspace_id=workspace_id,
                incident_id=occurrence.incident_id,
                occurrence_id=occurrence.id,
            )
        await self.session.execute(
            update(IngestionEvent)
            .where(IngestionEvent.id == event_id)
            .values(outcome="accepted" if created else "correlated", occurrence_id=occurrence.id)
        )
        if incident.event_kind == "recovered" or not created:
            await self.session.commit()
            return IntakeResult(
                outcome="accepted" if created else "correlated",
                workspace_id=workspace_id,
                incident_id=incident_row.id,
                occurrence_id=occurrence.id,
            )
        result = await self._create_investigation(
            workspace_id=workspace_id,
            incident=incident,
            occurrence_id=occurrence.id,
            incident_id=incident_row.id,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
            created_by=None,
            trigger_reason="initial",
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
        if retry_of_id is not None:
            parent = await self.session.get(Investigation, retry_of_id)
            if parent is None or parent.workspace_id != workspace_id:
                raise ValueError("Retry investigation ownership is invalid")
            result = await self._create_investigation(
                workspace_id=workspace_id,
                incident=incident,
                occurrence_id=parent.trigger_occurrence_id,
                incident_id=parent.incident_id,
                trace_ciphertext=trace_ciphertext,
                trace_hash=trace_hash,
                created_by=created_by,
                retry_of_id=retry_of_id,
                trigger_reason="retry",
            )
            await self.session.commit()
            return result
        incident_row, occurrence, _created, _duplicate = await self._record_occurrence(
            workspace_id=workspace_id,
            incident=incident,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
        )
        if incident.event_kind == "recovered":
            await self.session.commit()
            return IntakeResult(
                outcome="correlated",
                workspace_id=workspace_id,
                incident_id=incident_row.id,
                occurrence_id=occurrence.id,
            )
        result = await self._create_investigation(
            workspace_id=workspace_id,
            incident=incident,
            occurrence_id=occurrence.id,
            incident_id=incident_row.id,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
            created_by=created_by,
            trigger_reason="operator_request",
        )
        await self.session.commit()
        return result

    async def start_investigation_for_incident(
        self, *, incident_id: int, created_by: int
    ) -> IntakeResult:
        """Start an operator-requested run from the latest immutable occurrence."""

        incident_row = await self.session.get(Incident, incident_id)
        if incident_row is None:
            raise ValueError("Incident does not exist")
        occurrence = (
            await self.session.execute(
                select(IncidentOccurrence)
                .where(
                    IncidentOccurrence.incident_id == incident_id,
                    IncidentOccurrence.event_kind == "firing",
                )
                .order_by(IncidentOccurrence.occurred_at.desc(), IncidentOccurrence.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if occurrence is None:
            raise ValueError("Incident has no firing occurrence")
        trace_id = (
            decrypt_value(occurrence.trace_id_ciphertext)
            if occurrence.trace_id_ciphertext is not None
            else None
        )
        normalized = NormalizedIncident(
            source_type=occurrence.source_type,
            source_event_id=occurrence.source_event_id,
            dedup_key=occurrence.dedup_key,
            event_kind=occurrence.event_kind,
            occurred_at=occurrence.occurred_at,
            severity=occurrence.severity,
            event=occurrence.event,
            component=occurrence.component,
            environment=occurrence.environment,
            trace_id=trace_id,
            source_revision=occurrence.source_revision,
            error_masked=occurrence.error,
            raw_payload_masked=occurrence.raw_payload_masked,
            attachments_masked=(),
            masking_categories=(),
        )
        return await self._create_investigation(
            workspace_id=incident_row.workspace_id,
            incident=normalized,
            occurrence_id=occurrence.id,
            incident_id=incident_row.id,
            trace_ciphertext=occurrence.trace_id_ciphertext,
            trace_hash=occurrence.trace_id_hash,
            created_by=created_by,
            trigger_reason="operator_request",
        )

    async def retry_investigation(self, *, investigation_id: int, created_by: int) -> IntakeResult:
        """Create a new immutable run for a terminal run without another occurrence."""

        parent = await self.session.get(Investigation, investigation_id)
        if parent is None:
            raise ValueError("Investigation does not exist")
        if parent.status not in {"completed", "failed"}:
            raise ValueError("Only a terminal investigation can retry")
        occurrence_id = parent.trigger_occurrence_id
        if occurrence_id is None:
            occurrence = (
                await self.session.execute(
                    select(IncidentOccurrence)
                    .where(
                        IncidentOccurrence.incident_id == parent.incident_id,
                        IncidentOccurrence.event_kind == "firing",
                    )
                    .order_by(IncidentOccurrence.occurred_at.desc(), IncidentOccurrence.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        else:
            occurrence = await self.session.get(IncidentOccurrence, occurrence_id)
        if occurrence is None or occurrence.incident_id != parent.incident_id:
            raise ValueError("Retry investigation occurrence is unavailable")
        trace_id = (
            decrypt_value(occurrence.trace_id_ciphertext)
            if occurrence.trace_id_ciphertext is not None
            else None
        )
        normalized = NormalizedIncident(
            source_type=occurrence.source_type,
            source_event_id=occurrence.source_event_id,
            dedup_key=occurrence.dedup_key,
            event_kind=occurrence.event_kind,
            occurred_at=occurrence.occurred_at,
            severity=occurrence.severity,
            event=occurrence.event,
            component=occurrence.component,
            environment=occurrence.environment,
            trace_id=trace_id,
            source_revision=occurrence.source_revision,
            error_masked=occurrence.error,
            raw_payload_masked=occurrence.raw_payload_masked,
            attachments_masked=(),
            masking_categories=(),
        )
        return await self._create_investigation(
            workspace_id=parent.workspace_id,
            incident=normalized,
            occurrence_id=occurrence.id,
            incident_id=parent.incident_id,
            trace_ciphertext=occurrence.trace_id_ciphertext,
            trace_hash=occurrence.trace_id_hash,
            created_by=created_by,
            retry_of_id=parent.id,
            trigger_reason="retry",
        )

    async def _record_occurrence(
        self,
        *,
        workspace_id: int,
        incident: NormalizedIncident,
        trace_ciphertext: str | None,
        trace_hash: str | None,
    ) -> tuple[Incident, IncidentOccurrence, bool, bool]:
        """Persist one occurrence and atomically attach it to its operational incident."""

        if incident.source_event_id is not None:
            existing = (
                await self.session.execute(
                    select(IncidentOccurrence).where(
                        IncidentOccurrence.workspace_id == workspace_id,
                        IncidentOccurrence.source_event_id == incident.source_event_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                linked = await self.session.get(Incident, existing.incident_id)
                if linked is None:
                    raise RuntimeError("incident occurrence has no incident")
                return linked, existing, False, True

        now = datetime.now(UTC)
        created = False
        if incident.event_kind == "recovered":
            incident_row = (
                await self.session.execute(
                    select(Incident)
                    .where(
                        Incident.workspace_id == workspace_id,
                        Incident.dedup_key == incident.dedup_key,
                        Incident.state.in_(("open", "acknowledged", "mitigated")),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if incident_row is None:
                raise IncidentCorrelationError("recovery occurrence has no active incident")
        else:
            recurrence_of_id = (
                await self.session.execute(
                    select(Incident.id)
                    .where(
                        Incident.workspace_id == workspace_id,
                        Incident.dedup_key == incident.dedup_key,
                        Incident.state.in_(("resolved", "closed")),
                    )
                    .order_by(Incident.last_occurred_at.desc(), Incident.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            incident_id = (
                await self.session.execute(
                    pg_insert(Incident)
                    .values(
                        workspace_id=workspace_id,
                        dedup_key=incident.dedup_key,
                        event=incident.event,
                        component=incident.component,
                        environment=incident.environment,
                        severity=incident.severity,
                        state="open",
                        first_occurred_at=incident.occurred_at,
                        last_occurred_at=incident.occurred_at,
                        occurrence_count=1,
                        recurrence_of_id=recurrence_of_id,
                        state_changed_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["workspace_id", "dedup_key"],
                        index_where=text("state IN ('open', 'acknowledged', 'mitigated')"),
                    )
                    .returning(Incident.id)
                )
            ).scalar_one_or_none()
            created = incident_id is not None
            if created:
                incident_row = await self.session.get(Incident, incident_id)
                assert incident_row is not None
                self.session.add(
                    IncidentEvent(
                        incident_id=incident_row.id,
                        event_type="opened",
                        actor_id=None,
                        payload={
                            "dedup_key": incident.dedup_key,
                            "event": incident.event,
                            "component": incident.component,
                            "environment": incident.environment,
                            "recurrence_of_id": recurrence_of_id,
                        },
                    )
                )
            else:
                incident_row = (
                    await self.session.execute(
                        select(Incident)
                        .where(
                            Incident.workspace_id == workspace_id,
                            Incident.dedup_key == incident.dedup_key,
                            Incident.state.in_(("open", "acknowledged", "mitigated")),
                        )
                        .with_for_update()
                    )
                ).scalar_one()

        if incident.source_event_id is not None:
            existing = (
                await self.session.execute(
                    select(IncidentOccurrence).where(
                        IncidentOccurrence.workspace_id == workspace_id,
                        IncidentOccurrence.source_event_id == incident.source_event_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return incident_row, existing, False, True

        if not created:
            incident_row.last_occurred_at = max(incident_row.last_occurred_at, incident.occurred_at)
            incident_row.occurrence_count += 1
            if incident.severity == "CRITICAL":
                incident_row.severity = "CRITICAL"
            if incident.event_kind == "firing" and incident_row.state == "mitigated":
                incident_row.state = "open"
                incident_row.state_changed_at = now
                incident_row.state_version += 1
                self.session.add(
                    IncidentEvent(
                        incident_id=incident_row.id,
                        event_type="state_changed",
                        actor_id=None,
                        payload={
                            "command": "new_firing_occurrence",
                            "from_state": "mitigated",
                            "to_state": "open",
                            "state_version": incident_row.state_version,
                        },
                    )
                )
            elif incident.event_kind == "recovered" and incident_row.state != "mitigated":
                previous_state = incident_row.state
                incident_row.state = "mitigated"
                incident_row.state_changed_at = now
                incident_row.state_version += 1
                self.session.add(
                    IncidentEvent(
                        incident_id=incident_row.id,
                        event_type="state_changed",
                        actor_id=None,
                        payload={
                            "command": "recovery_occurrence",
                            "from_state": previous_state,
                            "to_state": "mitigated",
                            "state_version": incident_row.state_version,
                        },
                    )
                )

        occurrence = IncidentOccurrence(
            workspace_id=workspace_id,
            incident_id=incident_row.id,
            source_type=incident.source_type,
            source_event_id=incident.source_event_id,
            event_kind=incident.event_kind,
            dedup_key=incident.dedup_key,
            occurred_at=incident.occurred_at,
            severity=incident.severity,
            event=incident.event,
            component=incident.component,
            environment=incident.environment,
            trace_id_ciphertext=trace_ciphertext,
            trace_id_hash=trace_hash,
            source_revision=incident.source_revision,
            error=incident.error_masked,
            raw_payload_masked=incident.raw_payload_masked,
        )
        self.session.add(occurrence)
        await self.session.flush()
        self.session.add(
            IncidentEvent(
                incident_id=incident_row.id,
                event_type="occurrence_added",
                actor_id=None,
                payload={
                    "occurrence_id": occurrence.id,
                    "event_kind": incident.event_kind,
                    "source_type": incident.source_type,
                    "occurred_at": incident.occurred_at.isoformat(),
                },
            )
        )
        return incident_row, occurrence, created, False

    async def _create_investigation(
        self,
        *,
        workspace_id: int,
        incident: NormalizedIncident,
        occurrence_id: int | None,
        incident_id: int,
        trace_ciphertext: str | None,
        trace_hash: str | None,
        created_by: int | None,
        retry_of_id: int | None = None,
        trigger_reason: Literal[
            "initial", "severity_escalation", "evidence_change", "operator_request", "retry"
        ] = "initial",
    ) -> IntakeResult:
        workspace = await self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace does not exist")
        if retry_of_id is None:
            platform_settings = await self.session.get(PlatformSettings, 1)
            if platform_settings is None:
                raise ValueError("Platform settings are unavailable")
            output_language = platform_settings.ai_output_language
            limits = INVESTIGATION_HARD_LIMITS
            window_started_at = incident.occurred_at - timedelta(
                seconds=limits.window_before_seconds
            )
            window_finished_at = incident.occurred_at + timedelta(
                seconds=limits.window_after_seconds
            )
            execution_budget = investigation_execution_budget()
        else:
            parent = await self.session.get(Investigation, retry_of_id)
            if parent is None or parent.workspace_id != workspace_id:
                raise ValueError("Retry investigation ownership is invalid")
            output_language = parent.output_language
            window_started_at = parent.window_started_at
            window_finished_at = parent.window_finished_at
            execution_budget = dict(parent.execution_budget)
        investigation = Investigation(
            workspace_id=workspace_id,
            incident_id=incident_id,
            trigger_occurrence_id=occurrence_id,
            retry_of_id=retry_of_id,
            trigger_signature_hash=_trigger_signature(workspace_id, incident),
            trigger_reason=trigger_reason,
            output_language=output_language,
            window_started_at=window_started_at,
            window_finished_at=window_finished_at,
            execution_budget=execution_budget,
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
        input_content = _incident_evidence_content(incident, trace_value_ref)
        input_hash = canonical_hash(input_content)
        archived_at = datetime.now(UTC)
        input_collection = EvidenceCollection(
            investigation_id=investigation.id,
            operation_id=None,
            connector_snapshot_id=None,
            collection_kind="input",
            status="succeeded",
            fingerprint=canonical_hash(
                {
                    "investigation_id": investigation.id,
                    "artifact_kind": "incident_input",
                    "content_hash": input_hash,
                }
            ),
            purpose="Archive the immutable normalized incident input.",
            selector_masked={"investigation_input_id": investigation.id},
            artifact_count=1,
            result_bytes=len(
                json.dumps(
                    input_content,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ),
            started_at=archived_at,
            finished_at=archived_at,
        )
        self.session.add(input_collection)
        await self.session.flush()
        self.session.add(
            EvidenceArtifact(
                investigation_id=investigation.id,
                collection_id=input_collection.id,
                artifact_kind="incident_input",
                evidence_class="input",
                content_masked=input_content,
                content_hash=input_hash,
                provenance={
                    "source_type": "investigation_input",
                    "source_id": investigation.id,
                    "masking_categories": list(incident.masking_categories),
                },
                source_time_start=incident.occurred_at,
                source_time_end=incident.occurred_at,
                source_revision=incident.source_revision,
                data_class="masked",
                prompt_injection_markers=[],
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
        analysis_current = await _repository_analysis_current(self.session, workspace_id)
        graph_revision = (
            await self.session.execute(
                select(ResourceGraphRevision)
                .where(ResourceGraphRevision.workspace_id == workspace_id)
                .order_by(ResourceGraphRevision.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if analysis_current is False:
            graph_revision = None
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
                        "repository_analysis_current": analysis_current is not False,
                    }
                ),
            )
        )
        await InvestigationControlSnapshotStore(self.session).freeze(
            investigation_id=investigation.id,
            workspace_id=workspace_id,
            incident_source_revision=incident.source_revision,
            incident_source_type=incident.source_type,
        )
        await ConnectorSnapshotStore.freeze_in_session(self.session, investigation.id)
        job = InvestigationJob(investigation_id=investigation.id)
        self.session.add(job)
        self.session.add(
            IncidentEvent(
                incident_id=incident_id,
                event_type="investigation_started",
                actor_id=created_by,
                payload={
                    "investigation_id": investigation.id,
                    "occurrence_id": occurrence_id,
                    "trigger_reason": trigger_reason,
                    "retry_of_id": retry_of_id,
                },
            )
        )
        await self.session.flush()
        return IntakeResult(
            outcome="accepted",
            workspace_id=workspace_id,
            incident_id=incident_id,
            occurrence_id=occurrence_id,
            investigation_id=investigation.id,
            job_id=job.id,
        )
