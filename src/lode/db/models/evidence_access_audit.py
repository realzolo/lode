"""Immutable candidate, policy, authorization, and execution audit."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, snowflake_pk


class NativeReadCandidate(CreatedAtMixin, Base):
    __tablename__ = "native_read_candidates"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigation_operations.id", ondelete="CASCADE"), nullable=False
    )
    connector_snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigation_connector_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    model_invocation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_invocations.id", ondelete="RESTRICT"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="native-read-candidate.v1"
    )
    action_id: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expected_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_anchors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    payload_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    value_bindings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    requested_window: Mapped[dict | None] = mapped_column(JSONB)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("schema_version = 'native-read-candidate.v1'", name="schema_version"),
        CheckConstraint(
            "language IN ('logql', 'elasticsearch_query_dsl', 'opensearch_query_dsl', "
            "'sql', 'https', 'command')",
            name="language",
        ),
        CheckConstraint("cardinality(evidence_anchors) > 0", name="anchors_nonempty"),
        CheckConstraint("requested_limit > 0", name="limit_positive"),
        CheckConstraint("requested_timeout_ms > 0", name="timeout_positive"),
        CheckConstraint("candidate_hash ~ '^[0-9a-f]{64}$'", name="candidate_hash_sha256"),
        UniqueConstraint("investigation_id", "candidate_hash", name="uq_native_read_candidate_hash"),
        UniqueConstraint("operation_id", name="uq_native_read_candidate_operation"),
    )


class EvidenceAccessDecision(CreatedAtMixin, Base):
    __tablename__ = "evidence_access_decisions"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("native_read_candidates.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    parser_name: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    parse_tree_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_authorization_hash: Mapped[str] = mapped_column(Text, nullable=False)
    validation_decisions: Mapped[list] = mapped_column(JSONB, nullable=False)
    effective_action_masked: Mapped[dict | None] = mapped_column(JSONB)
    effective_budget: Mapped[dict | None] = mapped_column(JSONB)
    constraint_diff: Mapped[dict | None] = mapped_column(JSONB)
    rejection_code: Mapped[str | None] = mapped_column(Text)
    rejection_detail: Mapped[dict | None] = mapped_column(JSONB)
    decision_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("outcome IN ('allow', 'reject')", name="outcome"),
        CheckConstraint(
            "rejection_code IS NULL OR rejection_code IN ('invalid_syntax', 'unsupported_node', "
            "'write_semantics', 'scope_violation', 'budget_violation', "
            "'sandbox_violation', 'preflight_failed')",
            name="rejection_code",
        ),
        CheckConstraint(
            "(outcome = 'allow' AND rejection_code IS NULL AND effective_action_masked IS NOT NULL "
            "AND effective_budget IS NOT NULL) OR "
            "(outcome = 'reject' AND rejection_code IS NOT NULL AND effective_action_masked IS NULL "
            "AND effective_budget IS NULL)",
            name="outcome_payload",
        ),
        CheckConstraint("parse_tree_hash ~ '^[0-9a-f]{64}$'", name="parse_tree_hash_sha256"),
        CheckConstraint(
            "snapshot_authorization_hash ~ '^[0-9a-f]{64}$'",
            name="snapshot_authorization_hash_sha256",
        ),
        CheckConstraint("decision_hash ~ '^[0-9a-f]{64}$'", name="decision_hash_sha256"),
        UniqueConstraint("investigation_id", "decision_hash", name="uq_evidence_access_decision_hash"),
    )


class AuthorizedEvidenceRead(CreatedAtMixin, Base):
    __tablename__ = "authorized_evidence_reads"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    access_decision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evidence_access_decisions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    candidate_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    policy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    effective_action_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    effective_action_hash: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("candidate_hash ~ '^[0-9a-f]{64}$'", name="candidate_hash_sha256"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
        CheckConstraint("policy_hash ~ '^[0-9a-f]{64}$'", name="policy_hash_sha256"),
        CheckConstraint("effective_action_hash ~ '^[0-9a-f]{64}$'", name="action_hash_sha256"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_sha256"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256"),
        CheckConstraint("expires_at > issued_at", name="expiry_range"),
        UniqueConstraint("investigation_id", "fingerprint", name="uq_authorized_read_fingerprint"),
    )


class EvidenceReadAttempt(CreatedAtMixin, Base):
    __tablename__ = "evidence_read_attempts"

    id: Mapped[int] = snowflake_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    authorized_read_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("authorized_evidence_reads.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    preflight: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_artifact_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("attempt > 0", name="attempt_positive"),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'interrupted')",
            name="status",
        ),
        CheckConstraint("finished_at >= started_at", name="attempt_range"),
        CheckConstraint(
            "(status = 'succeeded' AND failure_code IS NULL AND failure_detail IS NULL) "
            "OR (status IN ('failed', 'interrupted') AND failure_code IS NOT NULL)",
            name="terminal_result",
        ),
        UniqueConstraint("authorized_read_id", "attempt", name="uq_evidence_read_attempt"),
        Index("ix_evidence_read_attempts_run", "investigation_id", "created_at"),
    )


class SealedEvidenceValue(CreatedAtMixin, Base):
    __tablename__ = "sealed_evidence_values"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    value_ref: Mapped[str] = mapped_column(Text, nullable=False)
    value_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    value_hash: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(Text, nullable=False)
    data_class: Mapped[str] = mapped_column(Text, nullable=False)
    source_artifact_id: Mapped[int | None] = mapped_column(BigInteger)
    envelope_key_version: Mapped[str] = mapped_column(Text, nullable=False)
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("investigation_id", "value_ref", name="uq_sealed_evidence_value_ref"),
        CheckConstraint("value_hash ~ '^[0-9a-f]{64}$'", name="value_hash_sha256"),
        CheckConstraint("btrim(value_ref) <> ''", name="value_ref_nonblank"),
        Index("ix_sealed_evidence_values_workspace", "workspace_id", "investigation_id"),
    )
