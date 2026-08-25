"""Canonical V1 models for sequential, evidence-backed investigations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, Text, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from typing import Any

from lode.db.base import Base


INVESTIGATION_STATUSES = ("queued", "running", "completed", "failed")
RESULT_STATES = ("pending", "confirmed", "hypothesis", "insufficient", "unavailable")
STEP_STATUSES = ("queued", "running", "succeeded", "partial", "blocked", "failed", "canceled")
OPERATION_STATUSES = STEP_STATUSES
OPERATION_EVENT_KINDS = ("started", "progress", "finished")
CODE_FINDING_STATUSES = ("confirmed", "hypothesis", "no_defect", "not_found")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, default=lambda: uuid.uuid4().hex)
    application_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alerts.id", ondelete="SET NULL"))
    incident_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incidents.id", ondelete="SET NULL"))
    trigger_signature: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    result_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    output_language: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    deployment_sha: Mapped[str | None] = mapped_column(Text)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    review_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    audit_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="auditable")
    engine_version: Mapped[str | None] = mapped_column(Text)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    event_cursor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retry_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="status"),
        CheckConstraint("result_state IN ('pending', 'confirmed', 'hypothesis', 'insufficient', 'unavailable')", name="result_state"),
        CheckConstraint("output_language IN ('en', 'zh')", name="output_language"),
        CheckConstraint("audit_status IN ('auditable', 'unverifiable', 'violated')", name="audit_status"),
        Index("ix_investigations_application_created", "application_id", "created_at"),
        Index("ix_investigations_incident", "incident_id"),
        Index("ix_investigations_retry_of", "retry_of_id"),
    )


class InvestigationInput(Base):
    __tablename__ = "investigation_inputs"

    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="Error")
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    error_stack: Mapped[str | None] = mapped_column(Text)
    error_cause: Mapped[Any | None] = mapped_column(JSONB)
    error_properties: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("source_type IN ('kafka', 'manual')", name="source_type"),
        CheckConstraint("severity IN ('CRITICAL', 'WARNING')", name="severity"),
    )


class InvestigationStep(Base):
    __tablename__ = "investigation_steps"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, default=lambda: uuid.uuid4().hex)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)
    expected_evidence: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tool_name: Mapped[str | None] = mapped_column(Text)
    tool_input: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    input_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    output_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    result_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("kind IN ('intake', 'triage', 'source', 'observability', 'dependency', 'evidence_request', 'synthesis')", name="kind"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'canceled')", name="status"),
        Index("uq_investigation_steps_ordinal", "investigation_id", "ordinal", unique=True),
        Index("uq_investigation_steps_running", "investigation_id", unique=True, postgresql_where=sql_text("status = 'running'")),
    )


class InvestigationDecision(Base):
    __tablename__ = "investigation_decisions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    after_step_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("investigation_steps.id", ondelete="SET NULL"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    selected_tool: Mapped[str | None] = mapped_column(Text)
    action_fingerprint: Mapped[str | None] = mapped_column(Text)
    rationale_summary: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("action IN ('execute', 'stop_confirmed', 'stop_hypothesis', 'stop_insufficient', 'stop_unavailable')", name="action"),
        Index("uq_investigation_decisions_ordinal", "investigation_id", "ordinal", unique=True),
        Index("uq_investigation_decisions_fingerprint", "investigation_id", "action_fingerprint", unique=True, postgresql_where=sql_text("action_fingerprint IS NOT NULL")),
    )


class InvestigationOperation(Base):
    __tablename__ = "investigation_operations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, default=lambda: uuid.uuid4().hex)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_steps.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    input_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    result_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("actor IN ('engine', 'ai', 'collector')", name="actor"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'canceled')", name="status"),
        Index("uq_investigation_operations_ordinal", "investigation_id", "ordinal", unique=True),
        Index("uq_investigation_operations_running", "investigation_id", unique=True, postgresql_where=sql_text("status = 'running'")),
        Index("ix_investigation_operations_step", "step_id", "ordinal"),
    )


class InvestigationOperationEvent(Base):
    __tablename__ = "investigation_operation_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_steps.id", ondelete="CASCADE"), nullable=False)
    operation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_operations.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("kind IN ('started', 'progress', 'finished')", name="kind"),
        Index("uq_investigation_operation_events_sequence", "investigation_id", "sequence", unique=True),
        Index("ix_investigation_operation_events_operation", "operation_id", "sequence"),
    )


class InvestigationAiInvocation(Base):
    __tablename__ = "investigation_ai_invocations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("investigation_steps.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_template_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    output_hash: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    token_source: Mapped[str] = mapped_column(Text, nullable=False, server_default="unavailable")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed', 'unavailable')", name="status"),
        CheckConstraint("token_source IN ('provider', 'estimated', 'unavailable')", name="token_source"),
        Index("ix_investigation_ai_invocations_run", "investigation_id", "step_id", "created_at"),
    )


class InvestigationFinding(Base):
    __tablename__ = "investigation_findings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("kind IN ('fact', 'hypothesis', 'counter_evidence', 'impact', 'evidence_gap', 'conclusion')", name="kind"),
        CheckConstraint("status IN ('supported', 'leading', 'contradicted', 'required', 'confirmed')", name="status"),
        Index("uq_investigation_findings_ordinal", "investigation_id", "ordinal", unique=True),
    )


class InvestigationFindingEdge(Base):
    __tablename__ = "investigation_finding_edges"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    from_finding_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_findings.id", ondelete="CASCADE"), nullable=False)
    to_finding_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_findings.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    __table_args__ = (
        CheckConstraint("relation IN ('supports', 'contradicts', 'caused_by', 'needs_test')", name="relation"),
        Index("ix_investigation_finding_edges_run", "investigation_id", "from_finding_id", "to_finding_id"),
    )


class InvestigationCodeFinding(Base):
    __tablename__ = "investigation_code_findings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    artifact_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("evidence_artifacts.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    repo_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("git_repos.id", ondelete="SET NULL"))
    revision: Mapped[str | None] = mapped_column(Text)
    revision_role: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    symbol: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    issue_type: Mapped[str | None] = mapped_column(Text)
    faulty_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    why_wrong: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_condition: Mapped[str] = mapped_column(Text, nullable=False)
    causal_chain: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    incident_evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    supporting_evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    counter_evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    missing_validation: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    fix_direction: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    test_scenario: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'hypothesis', 'no_defect', 'not_found')", name="status"),
        CheckConstraint("revision_role IS NULL OR revision_role IN ('incident', 'latest')", name="revision_role"),
        CheckConstraint("start_line IS NULL OR start_line > 0", name="start_line"),
        CheckConstraint("end_line IS NULL OR end_line >= start_line", name="end_line"),
        Index("ix_investigation_code_findings_run", "investigation_id", "status"),
    )


class InvestigationReport(Base):
    __tablename__ = "investigation_reports"

    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True)
    result_state: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    incident_cause: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    code_diagnosis: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    confirmed_facts: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    counter_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    evidence_gaps: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    next_step: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="investigation-report.v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (CheckConstraint("result_state IN ('confirmed', 'hypothesis', 'insufficient', 'unavailable')", name="result_state"),)


class InvestigationEvidenceLink(Base):
    __tablename__ = "investigation_evidence_links"

    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True)
    artifact_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("evidence_artifacts.id", ondelete="CASCADE"), primary_key=True)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("relation IN ('collected', 'manual')", name="relation"),
        Index("ix_investigation_evidence_links_artifact", "artifact_id"),
    )


class EvidenceCollection(Base):
    __tablename__ = "evidence_collections"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    step_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigation_steps.id", ondelete="CASCADE"), nullable=False)
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
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed')", name="status"),
        Index("ix_evidence_collections_investigation", "investigation_id", "step_id"),
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
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("artifact_type IN ('incident_input', 'source_file', 'source_diff', 'log', 'metric', 'trace', 'dependency', 'database', 'operator_input')", name="type"),
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
    resolution_basis: Mapped[str | None] = mapped_column(Text)
    origin_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    failure_detail: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("role IN ('incident', 'latest')", name="role"),
        CheckConstraint("status IN ('queued', 'resolved', 'unresolved', 'failed')", name="status"),
        Index("uq_source_revisions_run_repo_role", "investigation_id", "repo_id", "role", unique=True),
    )


class InvestigationJob(Base):
    __tablename__ = "investigation_jobs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, default=lambda: uuid.uuid4().hex)
    incident_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("incidents.id", ondelete="CASCADE"))
    investigation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'retry_wait', 'succeeded', 'dead')", name="status"),
        Index("ix_investigation_jobs_available", "status", "available_at", "priority", "created_at"),
        Index("uq_investigation_jobs_active_incident", "incident_id", unique=True, postgresql_where=sql_text("incident_id IS NOT NULL AND status IN ('queued', 'running', 'retry_wait')")),
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("now()"))

    __table_args__ = (
        CheckConstraint("kind IN ('loki', 'prometheus', 'tempo', 'postgres', 'redis', 'kafka', 'clickhouse')", name="kind"),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("collection_budget_seconds BETWEEN 1 AND 60", name="budget"),
        Index("ix_evidence_connectors_application", "application_id", "state"),
    )
