"""Kafka intake, deduplication, DLQ, incident, and job models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, TimestampMixin, snowflake_pk


class IngestionEvent(CreatedAtMixin, Base):
    __tablename__ = "ingestion_events"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    partition: Mapped[int] = mapped_column(Integer, nullable=False)
    offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence_id: Mapped[int | None] = mapped_column(BigInteger)
    dead_letter_id: Mapped[int | None] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("topic", "partition", "offset", name="uq_ingestion_event_position"),
        CheckConstraint("partition >= 0", name="partition_nonnegative"),
        CheckConstraint('"offset" >= 0', name="offset_nonnegative"),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash_sha256"),
        CheckConstraint(
            "outcome IN ('accepted', 'correlated', 'duplicate', 'dead_letter', 'unassigned')",
            name="outcome",
        ),
        Index("ix_ingestion_events_workspace_received", "workspace_id", "received_at"),
    )


class IncidentOccurrence(CreatedAtMixin, Base):
    """One immutable alert, recovery, or manually reported occurrence."""

    __tablename__ = "incident_occurrences"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(Text)
    event_kind: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(Text)
    trace_id_ciphertext: Mapped[str | None] = mapped_column(Text)
    trace_id_hash: Mapped[str | None] = mapped_column(Text)
    source_revision: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_payload_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("source_type IN ('kafka', 'manual')", name="source_type"),
        CheckConstraint("event_kind IN ('firing', 'recovered')", name="event_kind"),
        CheckConstraint("severity IN ('CRITICAL', 'WARNING')", name="severity"),
        CheckConstraint("length(dedup_key) > 0", name="dedup_key_nonempty"),
        CheckConstraint(
            "source_event_id IS NOT NULL OR source_type = 'manual'", name="source_event_id_required"
        ),
        CheckConstraint(
            "source_revision IS NULL OR source_revision ~ '^[0-9a-f]{40}$'",
            name="source_revision_sha",
        ),
        CheckConstraint(
            "trace_id_hash IS NULL OR trace_id_hash ~ '^[0-9a-f]{64}$'",
            name="trace_id_hash_sha256",
        ),
        Index(
            "uq_incident_occurrence_source_event",
            "workspace_id",
            "source_event_id",
            unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
        Index("ix_incident_occurrences_incident_occurred", "incident_id", "occurred_at"),
    )


class DeadLetter(TimestampMixin, Base):
    __tablename__ = "dead_letters"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    partition: Mapped[int | None] = mapped_column(Integer)
    offset: Mapped[int | None] = mapped_column(BigInteger)
    payload_masked: Mapped[dict | None] = mapped_column(JSONB)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    reason_detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    replayed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("partition IS NULL OR partition >= 0", name="partition_nonnegative"),
        CheckConstraint('"offset" IS NULL OR "offset" >= 0', name="offset_nonnegative"),
        Index(
            "uq_dead_letter_position",
            "topic",
            "partition",
            "offset",
            "kind",
            unique=True,
            postgresql_where=text('partition IS NOT NULL AND "offset" IS NOT NULL'),
        ),
        Index("ix_dead_letters_created_at", "created_at"),
    )


class Incident(TimestampMixin, Base):
    __tablename__ = "incidents"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    first_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    recurrence_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="SET NULL")
    )
    assigned_to: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("length(dedup_key) > 0", name="dedup_key_nonempty"),
        CheckConstraint("severity IN ('CRITICAL', 'WARNING')", name="severity"),
        CheckConstraint(
            "state IN ('open', 'acknowledged', 'mitigated', 'resolved', 'closed')", name="state"
        ),
        CheckConstraint("occurrence_count > 0", name="occurrence_count_positive"),
        CheckConstraint("last_occurred_at >= first_occurred_at", name="occurrence_range"),
        CheckConstraint("state_version > 0", name="state_version_positive"),
        Index(
            "uq_incident_active_dedup_key",
            "workspace_id",
            "dedup_key",
            unique=True,
            postgresql_where=text("state IN ('open', 'acknowledged', 'mitigated')"),
        ),
        Index("ix_incidents_workspace_state_updated", "workspace_id", "state", "updated_at"),
    )


class IncidentEvent(CreatedAtMixin, Base):
    """Append-only operational history for an incident."""

    __tablename__ = "incident_events"

    id: Mapped[int] = snowflake_pk()
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('opened', 'occurrence_added', 'state_changed', 'assigned', "
            "'investigation_started', 'review_recorded', 'action_created', 'action_updated')",
            name="event_type",
        ),
        Index("ix_incident_events_incident_created", "incident_id", "created_at"),
    )


class IncidentAction(TimestampMixin, Base):
    """A human-owned mitigation, remediation, verification, or prevention action."""

    __tablename__ = "incident_actions"

    id: Mapped[int] = snowflake_pk()
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    investigation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="SET NULL")
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="proposed")
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default="P2")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    validation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('mitigate', 'remediate', 'validate', 'prevent')", name="action_type"
        ),
        CheckConstraint(
            "status IN ('proposed', 'accepted', 'in_progress', 'verified', 'rejected', 'cancelled')",
            name="status",
        ),
        CheckConstraint("priority IN ('P0', 'P1', 'P2', 'P3')", name="priority"),
        CheckConstraint("length(title) > 0", name="title_nonempty"),
        Index("ix_incident_actions_incident_status", "incident_id", "status", "updated_at"),
    )


class InvestigationReview(CreatedAtMixin, Base):
    """An immutable human assessment of a report or one of its findings."""

    __tablename__ = "investigation_reviews"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    code_finding_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_code_findings.id", ondelete="CASCADE")
    )
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("verdict IN ('accepted', 'rejected', 'needs_evidence')", name="verdict"),
        CheckConstraint("length(comment) > 0", name="comment_nonempty"),
        Index("ix_investigation_reviews_investigation_created", "investigation_id", "created_at"),
    )


class InvestigationJob(TimestampMixin, Base):
    __tablename__ = "investigation_jobs"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    phase: Mapped[str] = mapped_column(Text, nullable=False, server_default="investigation")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    claimed_by: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_detail: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')", name="status"),
        CheckConstraint("phase IN ('investigation', 'reporting')", name="phase"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index(
            "ix_investigation_jobs_claim",
            "status",
            "phase",
            "available_at",
            "lease_expires_at",
        ),
    )
