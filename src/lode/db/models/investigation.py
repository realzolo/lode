"""Canonical production investigation models.

The investigation aggregate deliberately owns every execution record.  It has
no dependency on the retired analysis workflow: a run is auditable only when
its stages, collector attempts, source revisions, and evidence are persisted.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


STAGE_TYPES = ("ingest", "plan", "source", "observability", "dependencies", "reasoning", "resolution")
STAGE_STATUSES = ("queued", "running", "succeeded", "partial", "blocked", "failed", "not_configured")
EXECUTION_EVENT_PHASES = ("started", "succeeded", "partial", "blocked", "failed", "not_configured")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, default=lambda: uuid.uuid4().hex)
    application_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alerts.id", ondelete="SET NULL"))
    incident_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incidents.id", ondelete="SET NULL"))
    trigger_signature: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    output_language: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    deployment_sha: Mapped[str | None] = mapped_column(Text)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    conclusion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'completed', 'needs_review', 'failed')", name="status"),
        CheckConstraint("output_language IN ('en', 'zh')", name="output_language"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence"),
        Index("ix_investigations_application_created", "application_id", "created_at"),
        Index("ix_investigations_incident", "incident_id"),
    )


class InvestigationStage(Base):
    __tablename__ = "investigation_stages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    stage_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    output: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("stage_type IN ('ingest', 'plan', 'source', 'observability', 'dependencies', 'reasoning', 'resolution')", name="stage_type"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured')", name="status"),
        Index("uq_investigation_stages_run_type", "investigation_id", "stage_type", unique=True),
    )


class InvestigationExecutionEvent(Base):
    """An append-only, redacted fact about an operation inside a stage.

    A long-running operation emits a ``started`` record and one terminal record
    sharing ``operation_id``. This keeps live progress observable without
    rewriting historical events.
    """

    __tablename__ = "investigation_execution_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    stage_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_stages.id", ondelete="CASCADE"), nullable=False)
    collection_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("evidence_collections.id", ondelete="CASCADE"))
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    artifact_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint("phase IN ('started', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured')", name="phase"),
        Index("uq_investigation_execution_events_sequence", "investigation_id", "sequence", unique=True),
        Index("ix_investigation_execution_events_operation", "investigation_id", "stage_id", "operation_id", "sequence"),
    )


class EvidenceCollection(Base):
    __tablename__ = "evidence_collections"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    stage_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_stages.id", ondelete="CASCADE"), nullable=False)
    connector_kind: Mapped[str] = mapped_column(Text, nullable=False)
    connector_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    selector: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    config_hash: Mapped[str | None] = mapped_column(Text)
    collector_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1")
    artifact_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured')", name="status"),
        Index("ix_evidence_collections_investigation", "investigation_id", "stage_id"),
    )


class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifacts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    collection_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("evidence_collections.id", ondelete="SET NULL"))
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    locator: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    redacted_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint("artifact_type IN ('alert', 'source_file', 'source_diff', 'log', 'metric', 'trace', 'dependency', 'database')", name="type"),
        Index("ix_evidence_artifacts_investigation", "investigation_id", "collection_id"),
    )


class SourceRevision(Base):
    __tablename__ = "source_revisions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    repo_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("git_repos.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(Text, nullable=False)
    requested_ref: Mapped[str | None] = mapped_column(Text)
    resolved_sha: Mapped[str | None] = mapped_column(Text)
    origin_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    failure_detail: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint("role IN ('incident', 'latest')", name="role"),
        CheckConstraint("status IN ('queued', 'resolved', 'failed', 'not_configured')", name="status"),
        Index("uq_source_revisions_run_repo_role", "investigation_id", "repo_id", "role", unique=True),
    )


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'suspected', 'rejected', 'unknown')", name="status"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        Index("ix_hypotheses_investigation_rank", "investigation_id", "rank"),
    )


class RemediationPlan(Base):
    __tablename__ = "remediation_plans"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, unique=True)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    preconditions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    verification: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    rollback: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    agent_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')", name="risk"),)


class InvestigationJob(Base):
    __tablename__ = "investigation_jobs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, default=lambda: uuid.uuid4().hex)
    incident_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'retry_wait', 'succeeded', 'dead')", name="status"),
        Index("ix_investigation_jobs_available", "status", "available_at", "priority", "created_at"),
        Index("uq_investigation_jobs_active_incident", "incident_id", unique=True, postgresql_where=text("status IN ('queued', 'running', 'retry_wait')")),
    )


class EvidenceConnector(Base):
    __tablename__ = "evidence_connectors"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    application_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    secret_ref: Mapped[str | None] = mapped_column(Text)
    diagnostic_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    collection_budget_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="15")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    __table_args__ = (
        CheckConstraint("kind IN ('loki', 'prometheus', 'tempo', 'postgres', 'redis', 'kafka', 'clickhouse')", name="kind"),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("collection_budget_seconds BETWEEN 1 AND 60", name="budget"),
        Index("ix_evidence_connectors_application", "application_id", "state"),
    )
