"""Investigation roots, immutable snapshots, waves, and model context."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, TimestampMixin, identity_pk


class Investigation(TimestampMixin, Base):
    __tablename__ = "investigations"

    id: Mapped[int] = identity_pk()
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    investigation_policy_revision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("investigation_policy_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    alert_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("alerts.id", ondelete="SET NULL")
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="SET NULL")
    )
    retry_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="SET NULL")
    )
    trigger_signature_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    result_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    output_language: Mapped[str] = mapped_column(Text, nullable=False, server_default="en")
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_budget: Mapped[dict] = mapped_column(JSONB, nullable=False)
    budget_usage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    event_cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("trigger_signature_hash ~ '^[0-9a-f]{64}$'", name="trigger_hash_sha256"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="status"),
        CheckConstraint(
            "result_state IN ('pending', 'confirmed', 'hypothesis', 'insufficient', 'unavailable')",
            name="result_state",
        ),
        CheckConstraint("output_language IN ('en', 'zh')", name="output_language"),
        CheckConstraint("window_finished_at > window_started_at", name="window_range"),
        CheckConstraint("event_cursor >= 0", name="event_cursor_nonnegative"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="run_range",
        ),
        Index("ix_investigations_workspace_created", "workspace_id", "created_at"),
        Index("ix_investigations_incident", "incident_id"),
        Index("ix_investigations_retry_of", "retry_of_id"),
    )


class InvestigationInput(CreatedAtMixin, Base):
    __tablename__ = "investigation_inputs"

    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trace_value_ref: Mapped[str | None] = mapped_column(Text)
    source_revision: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_payload_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attachments_masked: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("source_type IN ('kafka', 'manual')", name="source_type"),
        CheckConstraint("severity IN ('CRITICAL', 'WARNING')", name="severity"),
        CheckConstraint(
            "source_revision IS NULL OR source_revision ~ '^[0-9a-f]{40}$'",
            name="source_revision_sha",
        ),
    )


class InvestigationRepositorySnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_repository_snapshots"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    repository_binding_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workspace_repository_bindings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_repositories.id", ondelete="RESTRICT"), nullable=False
    )
    account_connection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    credential_revision_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("git_account_credential_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_candidate_sha: Mapped[str | None] = mapped_column(Text)
    frozen_revision_role: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_resolution_status: Mapped[str] = mapped_column(Text, nullable=False)
    repository_identity_hash: Mapped[str] = mapped_column(Text, nullable=False)
    credential_identity_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["credential_revision_id", "account_connection_id"],
            [
                "git_account_credential_revisions.id",
                "git_account_credential_revisions.account_connection_id",
            ],
            ondelete="RESTRICT",
            name="fk_investigation_repository_snapshots_credential_account",
        ),
        UniqueConstraint(
            "investigation_id", "repository_binding_id", name="uq_investigation_repository_snapshot"
        ),
        CheckConstraint(
            "role IN ('runtime_source', 'shared_library', 'infrastructure', 'documentation')",
            name="role",
        ),
        CheckConstraint("binding_revision > 0", name="binding_revision_positive"),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint(
            "frozen_candidate_sha IS NULL OR frozen_candidate_sha ~ '^[0-9a-f]{40}$'",
            name="candidate_sha",
        ),
        CheckConstraint(
            "frozen_revision_role IN ('incident_source', 'repository_search_candidate')",
            name="frozen_revision_role",
        ),
        CheckConstraint(
            "frozen_resolution_status IN ('exact', 'unverified', 'unresolved')",
            name="frozen_resolution_status",
        ),
        CheckConstraint(
            "repository_identity_hash ~ '^[0-9a-f]{64}$'", name="repository_hash_sha256"
        ),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationBuildUnitSnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_build_unit_snapshots"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    build_unit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("build_units.id", ondelete="RESTRICT"), nullable=False
    )
    repository_snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("investigation_repository_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_root: Mapped[str] = mapped_column(Text, nullable=False)
    build_system: Mapped[str] = mapped_column(Text, nullable=False)
    identity_status: Mapped[str] = mapped_column(Text, nullable=False)
    build_unit_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "build_unit_id", name="uq_investigation_build_unit_snapshot"
        ),
        CheckConstraint(
            "identity_status IN ('verified', 'provisional', 'ambiguous')", name="identity_status"
        ),
        CheckConstraint("build_unit_revision > 0", name="revision_positive"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationComponentSnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_component_snapshots"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    component_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("components.id", ondelete="RESTRICT"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    identity_status: Mapped[str] = mapped_column(Text, nullable=False)
    component_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_bindings: Mapped[list] = mapped_column(JSONB, nullable=False)
    identity_aliases: Mapped[list] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "component_id", name="uq_investigation_component_snapshot"
        ),
        CheckConstraint(
            "identity_status IN ('verified', 'provisional', 'ambiguous')", name="identity_status"
        ),
        CheckConstraint("component_revision > 0", name="revision_positive"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationConnectorSnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_connector_snapshots"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evidence_connectors.id", ondelete="RESTRICT"), nullable=False
    )
    access_scope_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evidence_access_scopes.id", ondelete="RESTRICT"), nullable=False
    )
    connector_kind: Mapped[str] = mapped_column(Text, nullable=False)
    connector_kind_version: Mapped[int] = mapped_column(Integer, nullable=False)
    instance_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    access_scope_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    verification_status: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_introspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    allowed_languages: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    config_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scope_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_catalog: Mapped[dict] = mapped_column(JSONB, nullable=False)
    execution_budget_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    credential_identity_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "connector_id", name="uq_investigation_connector_snapshot"
        ),
        CheckConstraint("connector_kind_version > 0", name="kind_version_positive"),
        CheckConstraint("instance_revision > 0", name="instance_revision_positive"),
        CheckConstraint("access_scope_revision > 0", name="scope_revision_positive"),
        CheckConstraint("verification_status = 'healthy'", name="verification_healthy"),
        CheckConstraint("cardinality(capabilities) > 0", name="capabilities_nonempty"),
        CheckConstraint("cardinality(allowed_languages) > 0", name="languages_nonempty"),
        CheckConstraint(
            "credential_identity_hash ~ '^[0-9a-f]{64}$'", name="credential_hash_sha256"
        ),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationResourceGraphSnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_resource_graph_snapshots"

    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True
    )
    resource_graph_revision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("resource_graph_revisions.id", ondelete="RESTRICT")
    )
    graph_revision: Mapped[int | None] = mapped_column(Integer)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("graph_revision IS NULL OR graph_revision > 0", name="graph_rev_pos"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationDescriptorSnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_descriptor_snapshots"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    descriptor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    descriptor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    descriptor_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "investigation_id",
            "descriptor_kind",
            "descriptor_id",
            name="uq_investigation_descriptor_snapshot",
        ),
        CheckConstraint("descriptor_kind IN ('repository', 'component')", name="descriptor_kind"),
        CheckConstraint("descriptor_revision > 0", name="descriptor_rev_pos"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationModelPolicySnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_model_policy_snapshots"

    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True
    )
    model_policy_revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_policy_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    context_policy_revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("context_policy_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    model_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    context_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("model_policy_revision > 0", name="model_revision_positive"),
        CheckConstraint("context_policy_revision > 0", name="context_rev_pos"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationModelBindingSnapshot(CreatedAtMixin, Base):
    __tablename__ = "investigation_model_binding_snapshots"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_model_binding_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspace_model_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    provider_account_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_account_models.id", ondelete="RESTRICT"), nullable=False
    )
    provider_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_provider_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_account_model_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_account_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_classes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    allowed_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    routing_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "investigation_id",
            "workspace_model_binding_id",
            name="uq_investigation_model_binding_snapshot",
        ),
        CheckConstraint("binding_revision > 0", name="binding_rev_pos"),
        CheckConstraint("provider_account_model_revision > 0", name="account_model_rev_pos"),
        CheckConstraint("provider_account_revision > 0", name="account_rev_pos"),
        CheckConstraint("cardinality(execution_classes) > 0", name="classes_nonempty"),
        CheckConstraint("cardinality(allowed_roles) > 0", name="allowed_roles_nonempty"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash_sha256"),
    )


class InvestigationStep(CreatedAtMixin, Base):
    __tablename__ = "investigation_steps"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    hypothesis_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    output_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("ordinal > 0", name="ordinal_positive"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'interrupted')",
            name="status",
        ),
        UniqueConstraint("investigation_id", "ordinal", name="uq_investigation_step_ordinal"),
        Index(
            "uq_investigation_step_running",
            "investigation_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )


class InvestigationDecision(CreatedAtMixin, Base):
    __tablename__ = "investigation_decisions"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_steps.id", ondelete="SET NULL")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    hypotheses: Mapped[list] = mapped_column(JSONB, nullable=False)
    operation_plan: Mapped[list] = mapped_column(JSONB, nullable=False)
    next_model_hint: Mapped[dict | None] = mapped_column(JSONB)
    policy_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    policy_decisions: Mapped[list] = mapped_column(JSONB, nullable=False)
    selected_operation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_hash: Mapped[str] = mapped_column(Text, nullable=False)
    model_invocation_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("ordinal > 0", name="ordinal_positive"),
        CheckConstraint("decision IN ('continue', 'finish')", name="decision"),
        CheckConstraint("policy_outcome IN ('allow', 'trim', 'reject')", name="policy_outcome"),
        CheckConstraint("selected_operation_count BETWEEN 0 AND 4", name="operation_count"),
        CheckConstraint(
            "(decision = 'finish' AND selected_operation_count = 0) OR "
            "(decision = 'continue' AND selected_operation_count BETWEEN 1 AND 4)",
            name="decision_operation_count",
        ),
        CheckConstraint("decision_hash ~ '^[0-9a-f]{64}$'", name="decision_hash_sha256"),
        UniqueConstraint("investigation_id", "ordinal", name="uq_investigation_decision_ordinal"),
        UniqueConstraint(
            "investigation_id", "decision_hash", name="uq_investigation_decision_hash"
        ),
    )


class InvestigationOperation(CreatedAtMixin, Base):
    __tablename__ = "investigation_operations"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigation_steps.id", ondelete="CASCADE"), nullable=False
    )
    decision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigation_decisions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    wave_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    action_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expected_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_anchors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)
    stop_condition: Mapped[str] = mapped_column(Text, nullable=False)
    input_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    result_masked: Mapped[dict | None] = mapped_column(JSONB)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("ordinal > 0 AND wave_ordinal BETWEEN 1 AND 4", name="ordinals"),
        CheckConstraint(
            "operation_kind IN ('model', 'source_read', 'native_read', 'snapshot', 'validation', 'synthesis')",
            name="operation_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'rejected', 'failed', 'interrupted')",
            name="status",
        ),
        CheckConstraint("cardinality(evidence_anchors) > 0", name="anchors_nonempty"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_sha256"),
        UniqueConstraint("investigation_id", "ordinal", name="uq_investigation_operation_ordinal"),
        UniqueConstraint("step_id", "wave_ordinal", name="uq_step_operation_wave_ordinal"),
        UniqueConstraint(
            "investigation_id", "fingerprint", name="uq_investigation_operation_fingerprint"
        ),
        Index("ix_investigation_operations_step", "step_id", "wave_ordinal"),
    )


class InvestigationOperationEvent(Base):
    __tablename__ = "investigation_operation_events"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigation_operations.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail_masked: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "event_name IN ('operation.started', 'operation.progress', 'operation.finished')",
            name="event_name",
        ),
        UniqueConstraint(
            "investigation_id", "sequence", name="uq_investigation_operation_event_sequence"
        ),
        Index("ix_investigation_operation_events_operation", "operation_id", "sequence"),
    )


class ModelRoutingDecision(CreatedAtMixin, Base):
    __tablename__ = "model_routing_decisions"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    model_binding_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("investigation_model_binding_snapshots.id", ondelete="RESTRICT"),
    )
    execution_class: Mapped[str] = mapped_column(Text, nullable=False)
    required_context_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_candidates: Mapped[list] = mapped_column(JSONB, nullable=False)
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('planner', 'native_query', 'synthesizer', 'verifier', 'context_compactor')",
            name="role",
        ),
        CheckConstraint(
            "execution_class IN ('latency_optimized', 'reasoning_optimized')",
            name="execution_class",
        ),
        CheckConstraint("required_context_tokens > 0", name="required_context_positive"),
        CheckConstraint(
            "(model_binding_snapshot_id IS NULL AND allowed_input_tokens = 0 "
            "AND allowed_output_tokens = 0) OR "
            "(model_binding_snapshot_id IS NOT NULL AND allowed_output_tokens > 0 "
            "AND required_context_tokens <= allowed_input_tokens)",
            name="route_capacity",
        ),
        CheckConstraint("decision_hash ~ '^[0-9a-f]{64}$'", name="decision_hash_sha256"),
        UniqueConstraint(
            "investigation_id", "decision_hash", name="uq_model_routing_decision_hash"
        ),
        Index("ix_model_routing_decisions_run_role", "investigation_id", "role", "created_at"),
    )


class ContextBundleRevision(CreatedAtMixin, Base):
    __tablename__ = "context_bundle_revisions"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    routing_decision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_routing_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state_packet: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    summary_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    pinned_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    tokenizer_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_safety_margin_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('planner', 'native_query', 'synthesizer', 'verifier', 'context_compactor')",
            name="role",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("token_count >= 0", name="token_count_nonnegative"),
        CheckConstraint("reserved_output_tokens > 0", name="reserved_output_positive"),
        CheckConstraint("provider_safety_margin_tokens > 0", name="safety_margin_positive"),
        CheckConstraint("context_hash ~ '^[0-9a-f]{64}$'", name="context_hash_sha256"),
        UniqueConstraint("investigation_id", "role", "revision", name="uq_context_bundle_revision"),
        UniqueConstraint("investigation_id", "context_hash", name="uq_context_bundle_hash"),
    )


class ContextSummaryArtifact(CreatedAtMixin, Base):
    __tablename__ = "context_summary_artifacts"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    model_invocation_id: Mapped[int | None] = mapped_column(BigInteger)
    input_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    covered_claim_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    retained_counter_evidence_refs: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False
    )
    omitted_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    summary_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prompt_revision: Mapped[str] = mapped_column(Text, nullable=False)
    schema_revision: Mapped[str] = mapped_column(Text, nullable=False)
    tokenizer_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_status: Mapped[str] = mapped_column(Text, nullable=False)
    validation_detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("input_tokens > 0 AND output_tokens >= 0", name="token_counts"),
        CheckConstraint("validation_status IN ('valid', 'rejected')", name="validation_status"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256"),
        UniqueConstraint("investigation_id", "content_hash", name="uq_context_summary_hash"),
    )
