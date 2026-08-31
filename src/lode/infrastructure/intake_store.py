"""PostgreSQL persistence adapter for the intake use case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.intake import NormalizedSignal, canonical_hash, mask_failure_payload
from lode.application.investigation_limits import investigation_execution_budget
from lode.crypto import decrypt_value, encrypt_value
from lode.db.models import (
    DeadLetter,
    EvidenceArtifact,
    EvidenceCollection,
    GitRepository,
    Incident,
    IncidentCorrelationCandidate,
    IncidentCorrelationDecision,
    IncidentEvent,
    IncidentSignal,
    IncidentSignalAssociationEvent,
    IncidentSignalLink,
    IngestionEvent,
    Investigation,
    InvestigationControlEvent,
    InvestigationInput,
    InvestigationJob,
    InvestigationResourceGraphSnapshot,
    InvestigationSignalInput,
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
from lode.masking import mask_structure

IntakeOutcome = Literal["accepted", "correlated", "duplicate", "dead_letter", "unassigned"]


class IncidentCorrelationError(ValueError):
    """A valid intake event cannot be attached to an operational incident."""


@dataclass(frozen=True, slots=True)
class IntakeResult:
    outcome: IntakeOutcome
    workspace_id: int | None = None
    signal_id: int | None = None
    incident_id: int | None = None
    investigation_id: int | None = None
    job_id: int | None = None
    dead_letter_id: int | None = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _correlation_score(
    signal: NormalizedSignal,
    trace_hash: str | None,
    previous: IncidentSignal,
) -> tuple[float, dict[str, Any]]:
    """Return a deterministic, explainable association score."""

    if signal.source_type == "manual" and trace_hash is None and signal.repository_binding_id is None:
        return 0.0, {"reason": "manual_signal_has_no_correlation_anchors"}
    seconds = abs((signal.observed_at - previous.observed_at).total_seconds())
    trace_exact = trace_hash is not None and trace_hash == previous.trace_id_hash
    fingerprint_exact = signal.fingerprint == previous.fingerprint
    repository_exact = (
        signal.repository_binding_id is not None
        and signal.repository_binding_id == previous.repository_binding_id
    )
    factors = {
        "trace_exact": trace_exact,
        "fingerprint_exact": fingerprint_exact,
        "repository_exact": repository_exact,
        "repository_changed": bool(
            signal.repository_binding_id is not None
            and previous.repository_binding_id is not None
            and signal.repository_binding_id != previous.repository_binding_id
        ),
        "distance_seconds": seconds,
        "fingerprint_changed": not fingerprint_exact,
        "source_revision_changed": bool(
            signal.source_revision
            and previous.source_revision
            and signal.source_revision != previous.source_revision
        ),
    }
    if trace_exact:
        return 1.0, factors
    if signal.source_type == "kafka" and previous.source_type == "kafka":
        if fingerprint_exact and seconds <= 15 * 60:
            return 0.88, factors
        if fingerprint_exact and seconds <= 60 * 60:
            return 0.72, factors
    if repository_exact and fingerprint_exact and seconds <= 30 * 60:
        return 0.86, factors
    if repository_exact and fingerprint_exact and seconds <= 6 * 60 * 60:
        return 0.70, factors
    if fingerprint_exact and seconds <= 15 * 60:
        return 0.60, factors
    return 0.0, factors


def _trigger_signature(workspace_id: int, signal: NormalizedSignal) -> str:
    return canonical_hash(
        {
            "workspace_id": workspace_id,
            "fingerprint": signal.fingerprint,
            "signal_kind": signal.signal_kind,
            "observed_at": signal.observed_at.isoformat(),
        }
    )


def _incident_evidence_content(
    signal: NormalizedSignal, trace_value_ref: str | None, payload_value_ref: str
) -> dict[str, Any]:
    return {
        "schema_version": signal.schema_version,
        "source_type": signal.source_type,
        "source_event_id": signal.source_event_id,
        "signal_kind": signal.signal_kind,
        "title": signal.title,
        "summary": signal.summary,
        "repository_binding_id": signal.repository_binding_id,
        "severity": signal.severity,
        "observed_at": signal.observed_at.isoformat(),
        "fingerprint": signal.fingerprint,
        "trace_value_ref": trace_value_ref,
        "payload_value_ref": payload_value_ref,
        "source_revision": signal.source_revision,
        "error": signal.error_masked,
        "raw_payload": signal.raw_payload_masked,
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
            signal_id=row.signal_id,
            dead_letter_id=row.dead_letter_id,
        )

    async def _lock_ingestion_position(
        self, *, topic: str, partition: int, offset: int
    ) -> IngestionEvent | None:
        lock_key = int(
            canonical_hash({"topic": topic, "partition": partition, "offset": offset})[:15],
            16,
        )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
        )
        return (
            await self.session.execute(
                select(IngestionEvent).where(
                    IngestionEvent.topic == topic,
                    IngestionEvent.partition == partition,
                    IngestionEvent.offset == offset,
                )
            )
        ).scalar_one_or_none()

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
        existing_event = await self._lock_ingestion_position(
            topic=topic, partition=partition, offset=offset
        )
        if existing_event is not None:
            duplicate_result = IntakeResult(
                outcome="duplicate",
                workspace_id=existing_event.workspace_id,
                signal_id=existing_event.signal_id,
                dead_letter_id=existing_event.dead_letter_id,
            )
            await self.session.rollback()
            return duplicate_result

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
        signal: NormalizedSignal,
        replay_event_id: int | None = None,
        replay_dead_letter_id: int | None = None,
    ) -> IntakeResult:
        if (
            signal.source_event_id is None
            or signal.trace_id is None
            or signal.source_revision is None
        ):
            raise ValueError("Kafka v1 normalization requires alert, trace, and source revision")

        if replay_event_id is None:
            existing_event = await self._lock_ingestion_position(
                topic=topic, partition=partition, offset=offset
            )
            if existing_event is not None:
                duplicate_result = IntakeResult(
                    outcome="duplicate",
                    workspace_id=existing_event.workspace_id,
                    signal_id=existing_event.signal_id,
                    dead_letter_id=existing_event.dead_letter_id,
                )
                await self.session.rollback()
                return duplicate_result

        trace_ciphertext = (
            encrypt_value(signal.trace_id) if signal.trace_id is not None else None
        )
        trace_hash = _sha256(signal.trace_id) if signal.trace_id is not None else None
        incident_row, signal_row, created, duplicate, trigger_reason = await self._record_signal(
            workspace_id=workspace_id,
            signal=signal,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
        )
        event_outcome = "duplicate" if duplicate else "accepted" if created else "correlated"
        if replay_event_id is None:
            self.session.add(
                IngestionEvent(
                    workspace_id=workspace_id,
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    payload_hash=payload_hash,
                    source_event_id=signal.source_event_id,
                    outcome=event_outcome,
                    signal_id=signal_row.id,
                )
            )
        elif replay_dead_letter_id is not None:
            await self.session.execute(
                update(DeadLetter)
                .where(DeadLetter.id == replay_dead_letter_id, DeadLetter.replayed.is_(False))
                .values(replayed=True, replayed_at=datetime.now(UTC))
            )
        if duplicate:
            await self.session.commit()
            return IntakeResult(
                outcome="duplicate",
                workspace_id=workspace_id,
                incident_id=incident_row.id,
                signal_id=signal_row.id,
            )
        if trigger_reason is None:
            await self.session.commit()
            return IntakeResult(
                outcome="accepted" if created else "correlated",
                workspace_id=workspace_id,
                incident_id=incident_row.id,
                signal_id=signal_row.id,
            )
        result = await self._create_investigation(
            workspace_id=workspace_id,
            signal=signal,
            signal_id=signal_row.id,
            incident_id=incident_row.id,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
            created_by=None,
            trigger_reason=trigger_reason,
        )
        await self.session.commit()
        return result

    async def persist_manual(
        self,
        *,
        workspace_id: int,
        signal: NormalizedSignal,
        created_by: int,
    ) -> IntakeResult:
        if signal.source_type != "manual" or signal.idempotency_key_hash is None:
            raise ValueError("Manual intake requires a normalized manual signal")
        if signal.repository_binding_id is not None:
            binding = await self.session.get(
                WorkspaceRepositoryBinding, signal.repository_binding_id
            )
            if (
                binding is None
                or binding.workspace_id != workspace_id
                or binding.state != "active"
                or binding.analysis_mode != "code"
            ):
                raise ValueError("Selected error-service repository is not an active code binding")
        trace_ciphertext = (
            encrypt_value(signal.trace_id) if signal.trace_id is not None else None
        )
        trace_hash = _sha256(signal.trace_id) if signal.trace_id is not None else None
        incident_row, signal_row, _created, duplicate, _trigger = await self._record_signal(
            workspace_id=workspace_id,
            signal=signal,
            trace_ciphertext=trace_ciphertext,
            trace_hash=trace_hash,
        )
        if duplicate:
            await self.session.commit()
            return IntakeResult(
                outcome="duplicate",
                workspace_id=workspace_id,
                incident_id=incident_row.id,
                signal_id=signal_row.id,
            )
        result = await self._create_investigation(
            workspace_id=workspace_id,
            signal=signal,
            signal_id=signal_row.id,
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
        """Start an operator-requested run from the latest immutable signal."""

        incident_row = await self.session.get(Incident, incident_id)
        if incident_row is None:
            raise ValueError("Incident does not exist")
        signal_row = (
            await self.session.execute(
                select(IncidentSignal)
                .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                .where(
                    IncidentSignalLink.incident_id == incident_id,
                    IncidentSignal.signal_kind == "firing",
                )
                .order_by(IncidentSignal.observed_at.desc(), IncidentSignal.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if signal_row is None:
            raise ValueError("Incident has no firing signal")
        normalized = _normalized_from_signal(signal_row)
        return await self._create_investigation(
            workspace_id=incident_row.workspace_id,
            signal=normalized,
            signal_id=signal_row.id,
            incident_id=incident_row.id,
            trace_ciphertext=signal_row.trace_id_ciphertext,
            trace_hash=signal_row.trace_id_hash,
            created_by=created_by,
            trigger_reason="operator_request",
        )

    async def create_child_investigation(
        self,
        *,
        parent_investigation_id: int,
        created_by: int,
        command: Literal["resume", "add_evidence", "follow_up", "branch_hypothesis"],
        intervention: dict[str, Any],
    ) -> IntakeResult:
        """Create an immutable child run and append its operator provenance."""

        parent = (
            await self.session.execute(
                select(Investigation)
                .where(Investigation.id == parent_investigation_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if parent is None:
            raise ValueError("Investigation does not exist")
        if command == "resume" and parent.status != "paused":
            raise ValueError("Only a paused investigation can resume")
        if command != "resume" and parent.status in {"queued", "running", "reporting"}:
            raise ValueError("Add evidence, follow-up, and branches require a stopped run")

        signal_row = await self.session.get(IncidentSignal, parent.trigger_signal_id)
        signal_link = await self.session.get(IncidentSignalLink, parent.trigger_signal_id)
        if signal_row is None or signal_link is None or signal_link.incident_id != parent.incident_id:
            raise ValueError("Parent investigation signal is unavailable")
        masked_intervention, categories = mask_structure(intervention)
        normalized = _normalized_from_signal(
            signal_row,
            intervention=masked_intervention,
            sealed_intervention=intervention,
            masking_categories=categories,
        )
        trigger_reason = {
            "resume": "resumed",
            "add_evidence": "evidence_added",
            "follow_up": "follow_up",
            "branch_hypothesis": "hypothesis_branch",
        }[command]
        result = await self._create_investigation(
            workspace_id=parent.workspace_id,
            signal=normalized,
            signal_id=signal_row.id,
            incident_id=parent.incident_id,
            trace_ciphertext=signal_row.trace_id_ciphertext,
            trace_hash=signal_row.trace_id_hash,
            created_by=created_by,
            parent_investigation_id=parent.id,
            trigger_reason=trigger_reason,
        )
        self.session.add(
            InvestigationControlEvent(
                investigation_id=parent.id,
                command=command,
                actor_id=created_by,
                payload_masked=masked_intervention,
                child_investigation_id=result.investigation_id,
            )
        )
        self.session.add(
            IncidentEvent(
                incident_id=parent.incident_id,
                event_type="investigation_controlled",
                actor_id=created_by,
                payload={
                    "investigation_id": parent.id,
                    "command": command,
                    "child_investigation_id": result.investigation_id,
                },
            )
        )
        await self.session.commit()
        return result

    async def retry_investigation(self, *, investigation_id: int, created_by: int) -> IntakeResult:
        """Create a new immutable run for a terminal run without another occurrence."""

        parent = await self.session.get(Investigation, investigation_id)
        if parent is None:
            raise ValueError("Investigation does not exist")
        if parent.status not in {"completed", "failed"}:
            raise ValueError("Only a terminal investigation can retry")
        signal_id = parent.trigger_signal_id
        if signal_id is None:
            signal_row = (
                await self.session.execute(
                    select(IncidentSignal)
                    .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                    .where(
                        IncidentSignalLink.incident_id == parent.incident_id,
                        IncidentSignal.signal_kind == "firing",
                    )
                    .order_by(IncidentSignal.observed_at.desc(), IncidentSignal.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        else:
            signal_row = await self.session.get(IncidentSignal, signal_id)
        signal_link = (
            await self.session.get(IncidentSignalLink, signal_row.id)
            if signal_row is not None
            else None
        )
        if signal_row is None or signal_link is None or signal_link.incident_id != parent.incident_id:
            raise ValueError("Retry investigation signal is unavailable")
        normalized = _normalized_from_signal(signal_row)
        return await self._create_investigation(
            workspace_id=parent.workspace_id,
            signal=normalized,
            signal_id=signal_row.id,
            incident_id=parent.incident_id,
            trace_ciphertext=signal_row.trace_id_ciphertext,
            trace_hash=signal_row.trace_id_hash,
            created_by=created_by,
            parent_investigation_id=parent.id,
            trigger_reason="retry",
        )

    async def _record_signal(
        self,
        *,
        workspace_id: int,
        signal: NormalizedSignal,
        trace_ciphertext: str | None,
        trace_hash: str | None,
    ) -> tuple[Incident, IncidentSignal, bool, bool, str | None]:
        """Persist a signal and its explainable, Workspace-bounded association."""

        duplicate_filter = None
        if signal.source_event_id is not None:
            duplicate_filter = IncidentSignal.source_event_id == signal.source_event_id
        elif signal.idempotency_key_hash is not None:
            duplicate_filter = (
                IncidentSignal.idempotency_key_hash == signal.idempotency_key_hash
            )
        if duplicate_filter is not None:
            identity_lock = int(
                canonical_hash(
                    {
                        "workspace": workspace_id,
                        "source_event_id": signal.source_event_id,
                        "idempotency_key_hash": signal.idempotency_key_hash,
                    }
                )[:15],
                16,
            )
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": identity_lock}
            )
            existing = (
                await self.session.execute(
                    select(IncidentSignal, Incident)
                    .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                    .join(Incident, Incident.id == IncidentSignalLink.incident_id)
                    .where(
                        IncidentSignal.workspace_id == workspace_id, duplicate_filter
                    )
                )
            ).one_or_none()
            if existing is not None:
                existing_signal, linked = existing
                return linked, existing_signal, False, True, None

        # Serialize correlation decisions for the same Workspace/fingerprint pair.
        lock_key = int(canonical_hash({"workspace": workspace_id, "fp": signal.fingerprint})[:15], 16)
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

        active = (
            await self.session.execute(
                select(IncidentSignal, Incident)
                .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                .join(Incident, Incident.id == IncidentSignalLink.incident_id)
                .where(
                    Incident.workspace_id == workspace_id,
                    Incident.state.in_(("open", "acknowledged", "mitigated")),
                )
                .order_by(IncidentSignal.observed_at.desc(), IncidentSignal.id.desc())
                .limit(200)
                .with_for_update(of=Incident)
            )
        ).all()
        best_incident: Incident | None = None
        best_score = 0.0
        best_factors: dict[str, Any] = {}
        for previous, candidate_incident in active:
            score, factors = _correlation_score(signal, trace_hash, previous)
            if score > best_score:
                best_incident = candidate_incident
                best_score = score
                best_factors = factors

        created = best_incident is None or best_score < 0.85
        candidate_incident = best_incident if 0.60 <= best_score < 0.85 else None
        now = datetime.now(UTC)
        if created:
            recurrence_of_id = (
                await self.session.execute(
                    select(IncidentSignalLink.incident_id)
                    .join(IncidentSignal, IncidentSignal.id == IncidentSignalLink.signal_id)
                    .join(Incident, Incident.id == IncidentSignalLink.incident_id)
                    .where(
                        Incident.workspace_id == workspace_id,
                        Incident.state.in_(("resolved", "closed")),
                        IncidentSignal.fingerprint == signal.fingerprint,
                    )
                    .order_by(Incident.last_occurred_at.desc(), Incident.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            incident_row = Incident(
                workspace_id=workspace_id,
                title=signal.title,
                severity=signal.severity,
                state="open",
                first_occurred_at=signal.observed_at,
                last_occurred_at=signal.observed_at,
                signal_count=1,
                recurrence_of_id=recurrence_of_id,
                state_changed_at=now,
            )
            self.session.add(incident_row)
            await self.session.flush()
            self.session.add(
                IncidentEvent(
                    incident_id=incident_row.id,
                    event_type="opened",
                    actor_id=None,
                    payload={
                        "title": signal.title,
                        "source_type": signal.source_type,
                        "recurrence_of_id": recurrence_of_id,
                    },
                )
            )
            outcome = "candidate" if candidate_incident is not None else "new"
            decision_incident = candidate_incident or incident_row
            trigger_reason: str | None = "initial"
        else:
            assert best_incident is not None
            incident_row = best_incident
            previous_severity = incident_row.severity
            incident_row.last_occurred_at = max(incident_row.last_occurred_at, signal.observed_at)
            incident_row.signal_count += 1
            if signal.severity == "CRITICAL":
                incident_row.severity = "CRITICAL"
            if signal.signal_kind == "firing" and incident_row.state == "mitigated":
                incident_row.state = "open"
                incident_row.state_changed_at = now
                incident_row.state_version += 1
            elif signal.signal_kind == "recovered" and incident_row.state != "mitigated":
                incident_row.state = "mitigated"
                incident_row.state_changed_at = now
                incident_row.state_version += 1
            outcome = "auto_linked"
            decision_incident = incident_row
            trigger_reason = (
                "severity_escalation"
                if signal.severity == "CRITICAL" and previous_severity != "CRITICAL"
                else "recovery"
                if signal.signal_kind == "recovered"
                else "evidence_change"
                if best_factors.get("fingerprint_changed")
                or best_factors.get("repository_changed")
                or best_factors.get("source_revision_changed")
                else None
            )

        signal_row = IncidentSignal(
            workspace_id=workspace_id,
            schema_version=signal.schema_version,
            source_type=signal.source_type,
            source_event_id=signal.source_event_id,
            idempotency_key_hash=signal.idempotency_key_hash,
            signal_kind=signal.signal_kind,
            observed_at=signal.observed_at,
            severity=signal.severity,
            title=signal.title,
            summary=signal.summary,
            repository_binding_id=signal.repository_binding_id,
            trace_id_ciphertext=trace_ciphertext,
            trace_id_hash=trace_hash,
            source_revision=signal.source_revision,
            fingerprint=signal.fingerprint,
            error_masked=signal.error_masked,
            raw_payload_masked=signal.raw_payload_masked,
            raw_payload_ciphertext=encrypt_value(signal.sealed_payload),
            raw_payload_hash=_sha256(signal.sealed_payload),
        )
        self.session.add(signal_row)
        await self.session.flush()
        self.session.add(
            IncidentSignalLink(signal_id=signal_row.id, incident_id=incident_row.id)
        )
        decision = IncidentCorrelationDecision(
            workspace_id=workspace_id,
            signal_id=signal_row.id,
            incident_id=decision_incident.id,
            outcome=outcome,
            score=best_score if best_incident is not None else 0,
            factors=best_factors,
        )
        self.session.add(decision)
        await self.session.flush()
        self.session.add(
            IncidentSignalAssociationEvent(
                signal_id=signal_row.id,
                incident_id=incident_row.id,
                correlation_decision_id=decision.id,
                event_type="linked",
                actor_id=None,
                reason=outcome,
            )
        )
        if candidate_incident is not None:
            self.session.add(
                IncidentCorrelationCandidate(
                    workspace_id=workspace_id,
                    signal_id=signal_row.id,
                    candidate_incident_id=candidate_incident.id,
                    score=best_score,
                    factors=best_factors,
                )
            )
        self.session.add(
            IncidentEvent(
                incident_id=incident_row.id,
                event_type="signal_added",
                actor_id=None,
                payload={
                    "signal_id": signal_row.id,
                    "signal_kind": signal.signal_kind,
                    "source_type": signal.source_type,
                    "observed_at": signal.observed_at.isoformat(),
                    "correlation_outcome": outcome,
                    "correlation_score": best_score,
                },
            )
        )
        return incident_row, signal_row, created, False, trigger_reason

    async def _create_investigation(
        self,
        *,
        workspace_id: int,
        signal: NormalizedSignal,
        signal_id: int,
        incident_id: int,
        trace_ciphertext: str | None,
        trace_hash: str | None,
        created_by: int | None,
        parent_investigation_id: int | None = None,
        trigger_reason: Literal[
            "initial",
            "severity_escalation",
            "evidence_change",
            "recovery",
            "operator_request",
            "retry",
            "resumed",
            "evidence_added",
            "follow_up",
            "hypothesis_branch",
        ] = "initial",
    ) -> IntakeResult:
        workspace = await self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace does not exist")
        current = datetime.now(UTC)
        if parent_investigation_id is None and trigger_reason in {"evidence_change", "recovery"}:
            pending_delta = (
                await self.session.execute(
                    select(Investigation, InvestigationJob)
                    .join(
                        InvestigationJob,
                        InvestigationJob.investigation_id == Investigation.id,
                    )
                    .where(
                        Investigation.incident_id == incident_id,
                        Investigation.parent_investigation_id.is_(None),
                        Investigation.trigger_reason.in_(("evidence_change", "recovery")),
                        Investigation.status == "queued",
                        InvestigationJob.status == "pending",
                        InvestigationJob.available_at > current,
                    )
                    .order_by(Investigation.created_at.desc())
                    .limit(1)
                    .with_for_update(of=(Investigation, InvestigationJob))
                )
            ).one_or_none()
            if pending_delta is not None:
                pending_run, pending_job = pending_delta
                existing_input = await self.session.get(
                    InvestigationSignalInput,
                    {"investigation_id": pending_run.id, "signal_id": signal_id},
                )
                if existing_input is None:
                    self.session.add(
                        InvestigationSignalInput(
                            investigation_id=pending_run.id,
                            signal_id=signal_id,
                            input_role="delta",
                        )
                    )
                    await self._archive_coalesced_signal(
                        investigation_id=pending_run.id,
                        signal_id=signal_id,
                        signal=signal,
                    )
                pending_run.window_started_at = min(
                    pending_run.window_started_at,
                    signal.observed_at - timedelta(minutes=15),
                )
                pending_run.window_finished_at = max(
                    pending_run.window_finished_at,
                    signal.observed_at + timedelta(minutes=15),
                )
                if trigger_reason == "recovery":
                    pending_run.trigger_reason = "recovery"
                pending_job.available_at = current + timedelta(seconds=30)
                self.session.add(
                    IncidentEvent(
                        incident_id=incident_id,
                        event_type="investigation_started",
                        actor_id=created_by,
                        payload={
                            "investigation_id": pending_run.id,
                            "signal_id": signal_id,
                            "trigger_reason": trigger_reason,
                            "coalesced": True,
                        },
                    )
                )
                await self.session.flush()
                return IntakeResult(
                    outcome="accepted",
                    workspace_id=workspace_id,
                    incident_id=incident_id,
                    signal_id=signal_id,
                    investigation_id=pending_run.id,
                    job_id=pending_job.id,
                )
        if parent_investigation_id is None:
            platform_settings = await self.session.get(PlatformSettings, 1)
            if platform_settings is None:
                raise ValueError("Platform settings are unavailable")
            output_language = platform_settings.ai_output_language
            execution_budget = investigation_execution_budget()
        else:
            parent = await self.session.get(Investigation, parent_investigation_id)
            if parent is None or parent.workspace_id != workspace_id:
                raise ValueError("Parent investigation ownership is invalid")
            output_language = parent.output_language
            execution_budget = dict(parent.execution_budget)
        first_signal, last_signal = (
            await self.session.execute(
                select(
                    func.min(IncidentSignal.observed_at),
                    func.max(IncidentSignal.observed_at),
                )
                .select_from(IncidentSignal)
                .join(IncidentSignalLink, IncidentSignalLink.signal_id == IncidentSignal.id)
                .where(IncidentSignalLink.incident_id == incident_id)
            )
        ).one()
        first_observed = first_signal or signal.observed_at
        last_observed = last_signal or signal.observed_at
        window_started_at = first_observed - timedelta(seconds=15 * 60)
        window_finished_at = last_observed + timedelta(seconds=15 * 60)
        investigation = Investigation(
            workspace_id=workspace_id,
            incident_id=incident_id,
            trigger_signal_id=signal_id,
            parent_investigation_id=parent_investigation_id,
            trigger_signature_hash=_trigger_signature(workspace_id, signal),
            trigger_reason=trigger_reason,
            output_language=output_language,
            window_started_at=window_started_at,
            window_finished_at=window_finished_at,
            execution_budget=execution_budget,
            engine_version="lode",
        )
        self.session.add(investigation)
        await self.session.flush()

        trace_value_ref = "incident.trace_id" if signal.trace_id is not None else None
        payload_value_ref = "incident.input_payload"
        self.session.add(
            InvestigationInput(
                investigation_id=investigation.id,
                signal_id=signal_id,
                source_type=signal.source_type,
                title=signal.title,
                summary=signal.summary,
                severity=signal.severity,
                observed_at=signal.observed_at,
                repository_binding_id=signal.repository_binding_id,
                trace_value_ref=trace_value_ref,
                source_revision=signal.source_revision,
                error_masked=signal.error_masked,
                raw_payload_masked=signal.raw_payload_masked,
                created_by=created_by,
            )
        )
        self.session.add(
            InvestigationSignalInput(
                investigation_id=investigation.id,
                signal_id=signal_id,
                input_role="trigger",
            )
        )
        input_content = _incident_evidence_content(signal, trace_value_ref, payload_value_ref)
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
                    "masking_categories": list(signal.masking_categories),
                },
                source_time_start=signal.observed_at,
                source_time_end=signal.observed_at,
                source_revision=signal.source_revision,
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
        self.session.add(
            SealedEvidenceValue(
                workspace_id=workspace_id,
                investigation_id=investigation.id,
                value_ref=payload_value_ref,
                value_ciphertext=encrypt_value(signal.sealed_payload),
                value_hash=_sha256(signal.sealed_payload),
                value_type="json",
                data_class="sealed_incident_input",
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
            incident_source_revision=signal.source_revision,
            incident_source_type=signal.source_type,
        )
        await ConnectorSnapshotStore.freeze_in_session(self.session, investigation.id)
        available_at = datetime.now(UTC)
        if trigger_reason in {"evidence_change", "recovery"}:
            available_at += timedelta(seconds=30)
        job = InvestigationJob(investigation_id=investigation.id, available_at=available_at)
        self.session.add(job)
        self.session.add(
            IncidentEvent(
                incident_id=incident_id,
                event_type="investigation_started",
                actor_id=created_by,
                payload={
                    "investigation_id": investigation.id,
                    "signal_id": signal_id,
                    "trigger_reason": trigger_reason,
                    "parent_investigation_id": parent_investigation_id,
                },
            )
        )
        await self.session.flush()
        return IntakeResult(
            outcome="accepted",
            workspace_id=workspace_id,
            incident_id=incident_id,
            signal_id=signal_id,
            investigation_id=investigation.id,
            job_id=job.id,
        )

    async def _archive_coalesced_signal(
        self,
        *,
        investigation_id: int,
        signal_id: int,
        signal: NormalizedSignal,
    ) -> None:
        content = _incident_evidence_content(
            signal,
            "incident.trace_id" if signal.trace_id is not None else None,
            f"incident.signal.{signal_id}.sealed_payload",
        )
        content_hash = canonical_hash(content)
        archived_at = datetime.now(UTC)
        collection = EvidenceCollection(
            investigation_id=investigation_id,
            operation_id=None,
            connector_snapshot_id=None,
            collection_kind="input",
            status="succeeded",
            fingerprint=canonical_hash(
                {
                    "investigation_id": investigation_id,
                    "signal_id": signal_id,
                    "content_hash": content_hash,
                }
            ),
            purpose="Archive a signal coalesced into the pending delta run.",
            selector_masked={"signal_id": signal_id, "input_role": "delta"},
            artifact_count=1,
            result_bytes=len(
                json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ),
            started_at=archived_at,
            finished_at=archived_at,
        )
        self.session.add(collection)
        await self.session.flush()
        self.session.add(
            EvidenceArtifact(
                investigation_id=investigation_id,
                collection_id=collection.id,
                artifact_kind="incident_input",
                evidence_class="input",
                content_masked=content,
                content_hash=content_hash,
                provenance={
                    "source_type": "incident_signal",
                    "source_id": signal_id,
                    "input_role": "delta",
                    "masking_categories": list(signal.masking_categories),
                },
                source_time_start=signal.observed_at,
                source_time_end=signal.observed_at,
                source_revision=signal.source_revision,
                data_class="masked",
                prompt_injection_markers=[],
            )
        )


def _normalized_from_signal(
    signal: IncidentSignal,
    *,
    intervention: dict[str, Any] | None = None,
    sealed_intervention: dict[str, Any] | None = None,
    masking_categories: tuple[str, ...] = (),
) -> NormalizedSignal:
    trace_id = (
        decrypt_value(signal.trace_id_ciphertext)
        if signal.trace_id_ciphertext is not None
        else None
    )
    raw_payload_masked = dict(signal.raw_payload_masked)
    sealed_payload: dict[str, Any] = {"source_signal": signal.raw_payload_masked}
    if intervention is not None:
        raw_payload_masked["operator_intervention"] = intervention
        sealed_payload["operator_intervention"] = sealed_intervention or {}
    return NormalizedSignal(
        schema_version="incident-signal.v1",
        source_type=signal.source_type,
        source_event_id=signal.source_event_id,
        idempotency_key_hash=signal.idempotency_key_hash,
        signal_kind=signal.signal_kind,
        observed_at=signal.observed_at,
        severity=signal.severity,
        title=signal.title,
        summary=signal.summary,
        repository_binding_id=signal.repository_binding_id,
        trace_id=trace_id,
        source_revision=signal.source_revision,
        fingerprint=signal.fingerprint,
        error_masked=signal.error_masked,
        raw_payload_masked=raw_payload_masked,
        masking_categories=masking_categories,
        sealed_payload=json.dumps(sealed_payload, ensure_ascii=False, sort_keys=True),
    )
