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
    alert_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    alert_row_id: Mapped[int | None] = mapped_column(BigInteger)
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
            "outcome IN ('accepted', 'duplicate', 'dead_letter', 'unassigned')",
            name="outcome",
        ),
        Index("ix_ingestion_events_workspace_received", "workspace_id", "received_at"),
    )


class Alert(CreatedAtMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    alert_id: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_revision: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_payload_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "alert_id", name="uq_alert_workspace_id"),
        CheckConstraint("severity IN ('CRITICAL', 'WARNING')", name="severity"),
        CheckConstraint("source_revision ~ '^[0-9a-f]{40}$'", name="source_revision_sha"),
        CheckConstraint("trace_id_hash ~ '^[0-9a-f]{64}$'", name="trace_id_hash_sha256"),
        Index("ix_alerts_workspace_occurred", "workspace_id", "occurred_at"),
        Index("ix_alerts_event", "event"),
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
    signature_hash: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id_hash: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    first_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    latest_alert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("alerts.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("signature_hash ~ '^[0-9a-f]{64}$'", name="signature_hash_sha256"),
        CheckConstraint("trace_id_hash ~ '^[0-9a-f]{64}$'", name="trace_id_hash_sha256"),
        CheckConstraint("state IN ('active', 'closed')", name="state"),
        CheckConstraint("occurrence_count > 0", name="occurrence_count_positive"),
        CheckConstraint("last_occurred_at >= first_occurred_at", name="occurrence_range"),
        Index(
            "uq_incident_active_signature",
            "workspace_id",
            "signature_hash",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )


class InvestigationJob(TimestampMixin, Base):
    __tablename__ = "investigation_jobs"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
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
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index("ix_investigation_jobs_claim", "status", "available_at", "lease_expires_at"),
    )
