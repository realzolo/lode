"""Intake, incident, job, evidence and audit models.

These tables implement the production intake/execution contract:

* ``ingestion_events`` makes Kafka's at-least-once delivery idempotent: the
  unique ``(topic, partition, offset)`` triple guarantees a redelivered record
  can never create a second alert/analysis.
* ``incidents`` collapse repeated alerts that share an application-scoped
  ``dedupe_key`` into one operational unit.
* ``analysis_jobs`` are the durable, claimable unit of work. The consumer only
  *creates* them; a separate worker *claims* (``SKIP LOCKED``) and executes
  them. A partial unique index allows at most one active job per incident, which
  is what prevents the same error event from being analyzed repeatedly.
* ``evidence_artifacts`` are the citable, replayable proof behind every
  conclusion (Git commit/file, read-only DB query, deploy context, alert).
* ``audit_events`` are the append-only record of high-risk control-plane actions.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class IngestionEvent(Base):
    __tablename__ = "ingestion_events"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=True
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    partition: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offset: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    producer_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="accepted"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('accepted', 'dlq', 'unassigned')", name="status"
        ),
        Index(
            "uq_ingestion_events_topic_partition_offset",
            "topic",
            "partition",
            "offset",
            unique=True,
        ),
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    public_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, unique=True
    )
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="open"
    )
    first_alert_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )
    latest_alert_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reanalysis_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reanalysis_requested_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('open', 'resolved', 'suppressed')", name="state"
        ),
        Index(
            "uq_incidents_application_id_dedupe_key",
            "application_id",
            "dedupe_key",
            unique=True,
        ),
    )


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    public_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, unique=True
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    analysis_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="ingest"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="queued"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="5"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', "
            "'failed', 'canceled', 'dead')",
            name="status",
        ),
        CheckConstraint("attempt >= 0", name="attempt"),
        Index("ix_analysis_jobs_status_available", "status", "available_at", "priority", "created_at"),
        Index(
            "ix_analysis_jobs_lease_expires",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        # At most one active job per incident: prevents the same error event from
        # being analyzed more than once concurrently. A redelivered alert that
        # collides here is safely treated as a duplicate.
        Index(
            "uq_analysis_jobs_active_incident",
            "incident_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'running', 'retry_wait')"
            ),
        ),
    )


class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifacts"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    analysis_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    redacted_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('git_file', 'git_diff', 'db_query', "
            "'deploy', 'alert_payload')",
            name="artifact_type",
        ),
        Index("ix_evidence_artifacts_analysis_id", "analysis_id"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=False, server_default="ok")
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_actor_id", "actor_id"),
        Index("ix_audit_events_action", "action"),
    )


async def reap_expired_evidence(session) -> int:
    """Hard-delete evidence artifacts past their retention window (M3).

    Best-effort cleanup so stale DB-query / git evidence does not accumulate
    indefinitely. Called at startup alongside the shared-experience reaper; a
    transient DB error is the caller's responsibility to swallow so startup
    never blocks.
    """
    from datetime import UTC, datetime

    from sqlalchemy import delete

    now = datetime.now(UTC)
    result = await session.execute(
        delete(EvidenceArtifact)
        .where(EvidenceArtifact.retention_until.isnot(None))
        .where(EvidenceArtifact.retention_until < now)
    )
    await session.commit()
    return result.rowcount
