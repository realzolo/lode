"""Source authority, model invocation, code finding, and report models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, snowflake_pk


class SourceRevision(CreatedAtMixin, Base):
    __tablename__ = "source_revisions"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    repository_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("investigation_repository_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_origin: Mapped[str] = mapped_column(Text, nullable=False)
    requested_ref: Mapped[str | None] = mapped_column(Text)
    resolved_sha: Mapped[str | None] = mapped_column(Text)
    authority_status: Mapped[str] = mapped_column(Text, nullable=False)
    compatibility_status: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_basis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "revision_origin IN ('alert_revision', 'bound_branch_head', 'runtime_observed')",
            name="revision_origin",
        ),
        CheckConstraint(
            "authority_status IN ('authoritative', 'corroborated', 'contradicted', 'unavailable')",
            name="authority_status",
        ),
        CheckConstraint(
            "compatibility_status IN ('not_checked', 'compatible', 'incompatible')",
            name="compatibility_status",
        ),
        CheckConstraint(
            "resolved_sha IS NULL OR resolved_sha ~ '^[0-9a-f]{40}$'", name="resolved_sha"
        ),
        UniqueConstraint(
            "investigation_id",
            "repository_snapshot_id",
            "revision_origin",
            "resolved_sha",
            name="uq_source_revision",
        ),
    )


class SourceAssessment(CreatedAtMixin, Base):
    __tablename__ = "source_assessments"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    source_revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("source_revisions.id", ondelete="CASCADE"), nullable=False
    )
    build_unit_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_build_unit_snapshots.id", ondelete="SET NULL")
    )
    component_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_component_snapshots.id", ondelete="SET NULL")
    )
    authority_status: Mapped[str] = mapped_column(Text, nullable=False)
    compatibility_status: Mapped[str] = mapped_column(Text, nullable=False)
    mismatch_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    assessment_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "authority_status IN ('authoritative', 'corroborated', 'contradicted', 'unavailable')",
            name="authority_status",
        ),
        CheckConstraint(
            "compatibility_status IN ('not_checked', 'compatible', 'incompatible')",
            name="compatibility_status",
        ),
        CheckConstraint("assessment_hash ~ '^[0-9a-f]{64}$'", name="assessment_hash_sha256"),
        UniqueConstraint(
            "investigation_id", "source_revision_id", name="uq_source_assessment_revision"
        ),
        UniqueConstraint("investigation_id", "assessment_hash", name="uq_source_assessment_hash"),
    )


class InvestigationCodeFinding(CreatedAtMixin, Base):
    __tablename__ = "investigation_code_findings"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_artifact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("evidence_artifacts.id", ondelete="RESTRICT")
    )
    source_assessment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_assessments.id", ondelete="RESTRICT")
    )
    repository_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("git_repositories.id", ondelete="RESTRICT")
    )
    revision: Mapped[str | None] = mapped_column(Text)
    revision_origin: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    symbol: Mapped[str | None] = mapped_column(Text)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    issue_type: Mapped[str | None] = mapped_column(Text)
    faulty_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    why_wrong: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_condition: Mapped[str] = mapped_column(Text, nullable=False)
    propagation: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    incident_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    supporting_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    counter_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    missing_validation: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    test_scenario: Mapped[str] = mapped_column(Text, nullable=False)
    verifier_invocation_id: Mapped[int | None] = mapped_column(BigInteger)
    finding_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('confirmed', 'hypothesis', 'no_defect', 'not_found')",
            name="status",
        ),
        CheckConstraint(
            "revision_origin IS NULL OR revision_origin IN ('alert_revision', "
            "'bound_branch_head', 'runtime_observed')",
            name="revision_origin",
        ),
        CheckConstraint("revision IS NULL OR revision ~ '^[0-9a-f]{40}$'", name="revision_sha"),
        CheckConstraint("start_line IS NULL OR start_line > 0", name="start_line_positive"),
        CheckConstraint("end_line IS NULL OR end_line >= start_line", name="line_range"),
        CheckConstraint(
            "status IN ('no_defect', 'not_found') OR "
            "(source_artifact_id IS NOT NULL AND source_assessment_id IS NOT NULL "
            "AND repository_id IS NOT NULL AND revision IS NOT NULL AND path IS NOT NULL "
            "AND symbol IS NOT NULL AND start_line IS NOT NULL AND end_line IS NOT NULL)",
            name="finding_source_anchor",
        ),
        CheckConstraint("finding_hash ~ '^[0-9a-f]{64}$'", name="finding_hash_sha256"),
        UniqueConstraint("investigation_id", "finding_hash", name="uq_code_finding_hash"),
        Index("ix_investigation_code_findings_status", "investigation_id", "status"),
    )


class AIInvocation(CreatedAtMixin, Base):
    __tablename__ = "ai_invocations"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_operations.id", ondelete="SET NULL")
    )
    routing_decision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_routing_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    context_bundle_revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("context_bundle_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    provider_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_provider_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    provider_account_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_account_models.id", ondelete="RESTRICT"), nullable=False
    )
    provider_account_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_account_model_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_class: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_revision: Mapped[str] = mapped_column(Text, nullable=False)
    schema_revision: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    temperature: Mapped[float | None] = mapped_column(Numeric(6, 5))
    seed: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[float | None] = mapped_column(Numeric(18, 8))
    termination_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[dict | None] = mapped_column(JSONB)
    output_masked: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(
            "role IN ('resource_analyst', 'planner', 'native_query', 'synthesizer', 'verifier', 'context_compactor')",
            name="role",
        ),
        CheckConstraint(
            "execution_class IN ('latency_optimized', 'reasoning_optimized')",
            name="execution_class",
        ),
        CheckConstraint("status IN ('succeeded', 'failed', 'unavailable')", name="status"),
        CheckConstraint("provider_account_revision > 0", name="account_revision_positive"),
        CheckConstraint(
            "provider_account_model_revision > 0", name="account_model_revision_positive"
        ),
        CheckConstraint("attempt_count > 0", name="attempt_count_positive"),
        CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
        CheckConstraint("context_hash ~ '^[0-9a-f]{64}$'", name="context_hash_sha256"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_sha256"),
        CheckConstraint(
            "response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'", name="response_hash_sha256"
        ),
        Index("ix_ai_invocations_run_role", "investigation_id", "role", "created_at"),
    )


class InvestigationReport(CreatedAtMixin, Base):
    __tablename__ = "investigation_reports"

    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True
    )
    result_state: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact_scope: Mapped[list] = mapped_column(JSONB, nullable=False)
    causal_graph: Mapped[dict] = mapped_column(JSONB, nullable=False)
    code_finding_refs: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    participants: Mapped[list] = mapped_column(JSONB, nullable=False)
    timeline_summary: Mapped[list] = mapped_column(JSONB, nullable=False)
    source_assessments: Mapped[list] = mapped_column(JSONB, nullable=False)
    configuration_assessments: Mapped[list] = mapped_column(JSONB, nullable=False)
    counter_evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    evidence_gaps: Mapped[list] = mapped_column(JSONB, nullable=False)
    action_recommendations: Mapped[list] = mapped_column(JSONB, nullable=False)
    synthesizer_invocation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_invocations.id", ondelete="RESTRICT")
    )
    verifier_invocation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_invocations.id", ondelete="RESTRICT")
    )
    schema_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="investigation-report.v1"
    )
    report_hash: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "result_state IN ('confirmed', 'hypothesis', 'insufficient', 'unavailable')",
            name="result_state",
        ),
        CheckConstraint("schema_version = 'investigation-report.v1'", name="schema_version"),
        CheckConstraint("report_hash ~ '^[0-9a-f]{64}$'", name="report_hash_sha256"),
    )
