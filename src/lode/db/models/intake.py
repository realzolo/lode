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
    Numeric,
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
    signal_id: Mapped[int | None] = mapped_column(BigInteger)
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


class IncidentSignal(CreatedAtMixin, Base):
    """One immutable normalized Kafka alert or human error report."""

    __tablename__ = "incident_signals"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="incident-signal.v1"
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key_hash: Mapped[str | None] = mapped_column(Text)
    signal_kind: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    repository_binding_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workspace_repository_bindings.id", ondelete="RESTRICT")
    )
    trace_id_ciphertext: Mapped[str | None] = mapped_column(Text)
    trace_id_hash: Mapped[str | None] = mapped_column(Text)
    source_revision: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    error_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_payload_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("schema_version = 'incident-signal.v1'", name="schema_version"),
        CheckConstraint("source_type IN ('kafka', 'manual')", name="source_type"),
        CheckConstraint("signal_kind IN ('firing', 'recovered')", name="signal_kind"),
        CheckConstraint(
            "severity IN ('CRITICAL', 'WARNING', 'UNCLASSIFIED')", name="severity"
        ),
        CheckConstraint("length(title) > 0", name="title_nonempty"),
        CheckConstraint("length(summary) > 0", name="summary_nonempty"),
        CheckConstraint(
            "source_event_id IS NOT NULL OR source_type = 'manual'", name="source_event_id_required"
        ),
        CheckConstraint(
            "idempotency_key_hash IS NULL OR idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="idempotency_hash_sha256",
        ),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_sha256"),
        CheckConstraint("raw_payload_hash ~ '^[0-9a-f]{64}$'", name="raw_payload_hash_sha256"),
        CheckConstraint(
            "source_revision IS NULL OR source_revision ~ '^[0-9a-f]{40}$'",
            name="source_revision_sha",
        ),
        CheckConstraint(
            "trace_id_hash IS NULL OR trace_id_hash ~ '^[0-9a-f]{64}$'",
            name="trace_id_hash_sha256",
        ),
        Index(
            "uq_incident_signal_source_event",
            "workspace_id",
            "source_event_id",
            unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
        Index(
            "uq_incident_signal_idempotency",
            "workspace_id",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text("idempotency_key_hash IS NOT NULL"),
        ),
        Index("ix_incident_signals_workspace_fingerprint", "workspace_id", "fingerprint"),
    )


class IncidentSignalLink(TimestampMixin, Base):
    """Mutable current-association projection over immutable signals and events."""

    __tablename__ = "incident_signal_links"

    signal_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_signals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("state_version > 0", name="state_version_positive"),
        Index("ix_incident_signal_links_incident", "incident_id", "signal_id"),
    )


class IncidentCorrelationDecision(CreatedAtMixin, Base):
    """Immutable server decision explaining how a signal was associated."""

    __tablename__ = "incident_correlation_decisions"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident_signals.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    factors: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="v1")

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('new', 'auto_linked', 'candidate', 'operator_linked')", name="outcome"
        ),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("policy_version = 'v1'", name="policy_version"),
        Index("ix_incident_correlation_signal", "signal_id", "created_at"),
    )


class IncidentCorrelationCandidate(TimestampMixin, Base):
    __tablename__ = "incident_correlation_candidates"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    signal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident_signals.id", ondelete="CASCADE"), nullable=False
    )
    candidate_incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    factors: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    decided_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="status"),
        UniqueConstraint("signal_id", "candidate_incident_id", name="uq_signal_candidate"),
        Index("ix_incident_correlation_candidates_pending", "workspace_id", "status", "created_at"),
    )


class IncidentSignalAssociationEvent(CreatedAtMixin, Base):
    """Append-only signal association history used by merge and split projections."""

    __tablename__ = "incident_signal_association_events"

    id: Mapped[int] = snowflake_pk()
    signal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident_signals.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    correlation_decision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident_correlation_decisions.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("event_type IN ('linked', 'unlinked')", name="event_type"),
        CheckConstraint("length(reason) > 0", name="reason_nonempty"),
        Index("ix_signal_association_events_signal", "signal_id", "created_at"),
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
    title: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    first_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    recurrence_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="SET NULL")
    )
    assigned_to: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("length(title) > 0", name="title_nonempty"),
        CheckConstraint(
            "severity IN ('CRITICAL', 'WARNING', 'UNCLASSIFIED')", name="severity"
        ),
        CheckConstraint(
            "state IN ('open', 'acknowledged', 'mitigated', 'resolved', 'closed')", name="state"
        ),
        CheckConstraint("signal_count >= 0", name="signal_count_nonnegative"),
        CheckConstraint("last_occurred_at >= first_occurred_at", name="signal_range"),
        CheckConstraint("state_version > 0", name="state_version_positive"),
        Index("ix_incidents_workspace_state_updated", "workspace_id", "state", "updated_at"),
        Index("ix_incidents_workspace_last_observed", "workspace_id", "last_occurred_at"),
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
            "event_type IN ('opened', 'signal_added', 'signal_unlinked', 'incidents_merged', "
            "'incident_split', 'state_changed', 'severity_changed', 'assigned', "
            "'investigation_started', 'investigation_controlled', 'review_recorded', "
            "'action_created', 'action_updated')",
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
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default="P2")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    validation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('mitigate', 'remediate', 'validate', 'prevent')", name="action_type"
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'blocked', 'validation', 'completed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("priority IN ('P0', 'P1', 'P2', 'P3')", name="priority"),
        CheckConstraint("length(title) > 0", name="title_nonempty"),
        CheckConstraint("state_version > 0", name="state_version_positive"),
        Index("ix_incident_actions_incident_status", "incident_id", "status", "updated_at"),
    )


class IncidentActionProposal(CreatedAtMixin, Base):
    """Immutable evidence-bound action proposed by a verified report."""

    __tablename__ = "incident_action_proposals"

    id: Mapped[int] = snowflake_pk()
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    validation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    proposal_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('mitigate', 'remediate', 'validate', 'prevent')", name="action_type"
        ),
        CheckConstraint("priority IN ('P0', 'P1', 'P2', 'P3')", name="priority"),
        CheckConstraint("proposal_hash ~ '^[0-9a-f]{64}$'", name="proposal_hash_sha256"),
        UniqueConstraint("investigation_id", "proposal_hash", name="uq_action_proposal_hash"),
    )


class IncidentActionProposalDecision(CreatedAtMixin, Base):
    __tablename__ = "incident_action_proposal_decisions"

    id: Mapped[int] = snowflake_pk()
    proposal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incident_action_proposals.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    action_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incident_actions.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint("decision IN ('accepted', 'rejected')", name="decision"),
        CheckConstraint("length(reason) > 0", name="reason_nonempty"),
        UniqueConstraint("proposal_id", name="uq_action_proposal_decision"),
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
    supersedes_review_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_reviews.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint("verdict IN ('accepted', 'rejected', 'needs_evidence')", name="verdict"),
        CheckConstraint("length(comment) > 0", name="comment_nonempty"),
        UniqueConstraint("supersedes_review_id", name="uq_review_superseded_once"),
        Index("ix_investigation_reviews_investigation_created", "investigation_id", "created_at"),
    )


class IncidentKnowledgeCase(CreatedAtMixin, Base):
    """Append-only index entry for a human-accepted immutable report."""

    __tablename__ = "incident_knowledge_cases"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    accepted_review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigation_reviews.id", ondelete="RESTRICT"), nullable=False
    )
    report_hash: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    causal_signature: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    search_document: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("report_hash ~ '^[0-9a-f]{64}$'", name="report_hash_sha256"),
        UniqueConstraint("investigation_id", name="uq_knowledge_case_investigation"),
        UniqueConstraint("accepted_review_id", name="uq_knowledge_case_review"),
        Index("ix_incident_knowledge_cases_workspace_created", "workspace_id", "created_at"),
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
        CheckConstraint(
            "status IN ('pending', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
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
