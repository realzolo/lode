"""Global provider accounts and Workspace model portfolio."""

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
from lode.db.models._common import CreatedAtMixin, TimestampMixin, identity_pk


class AIProviderAccount(TimestampMixin, Base):
    __tablename__ = "ai_provider_accounts"

    id: Mapped[int] = identity_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    provider_kind: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    credential_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    organization_ref: Mapped[str | None] = mapped_column(Text)
    project_ref: Mapped[str | None] = mapped_column(Text)
    tenant_ref: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    verification_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="untested")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_limit_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cost_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    data_processing_policy_revision: Mapped[str] = mapped_column(Text, nullable=False)
    data_residency: Mapped[str] = mapped_column(Text, nullable=False)
    retention_mode: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint(
            "verification_status IN ('untested', 'healthy', 'unavailable')",
            name="verification_status",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class ProviderModelObservation(CreatedAtMixin, Base):
    __tablename__ = "provider_model_observations"

    id: Mapped[int] = identity_pk()
    provider_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_provider_accounts.id", ondelete="CASCADE"), nullable=False
    )
    provider_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    capability_hints: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    provider_payload_masked: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "provider_account_id", "provider_model_id", "response_hash",
            name="uq_provider_model_observation",
        ),
        CheckConstraint("response_hash ~ '^[0-9a-f]{64}$'", name="response_hash_sha256"),
    )


class ModelDeployment(TimestampMixin, Base):
    __tablename__ = "model_deployments"

    id: Mapped[int] = identity_pk()
    provider_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_provider_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    provider_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    tokenizer_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_revision: Mapped[str] = mapped_column(Text, nullable=False)
    availability_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="untested")
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality_baseline_revision: Mapped[str] = mapped_column(Text, nullable=False)
    cost_policy_revision: Mapped[str] = mapped_column(Text, nullable=False)
    rate_limit_policy_revision: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("provider_account_id", "provider_model_id", name="uq_model_deployment"),
        CheckConstraint("max_input_tokens > 0", name="max_input_tokens_positive"),
        CheckConstraint("max_output_tokens > 0", name="max_output_tokens_positive"),
        CheckConstraint(
            "availability_state IN ('untested', 'healthy', 'unavailable')",
            name="availability_state",
        ),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class ContextPolicyRevision(CreatedAtMixin, Base):
    __tablename__ = "context_policy_revisions"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    pinned_evidence_kinds: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    compression_levels: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    minimum_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_safety_margin_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "revision", name="uq_context_policy_revision"),
        CheckConstraint("cardinality(pinned_evidence_kinds) > 0", name="pinned_nonempty"),
        CheckConstraint("cardinality(compression_levels) > 0", name="compression_nonempty"),
        CheckConstraint("minimum_output_tokens > 0", name="output_tokens_positive"),
        CheckConstraint("provider_safety_margin_tokens > 0", name="margin_positive"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class WorkspaceModelBinding(TimestampMixin, Base):
    __tablename__ = "workspace_model_bindings"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    model_deployment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_deployments.id", ondelete="RESTRICT"), nullable=False
    )
    execution_classes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    allowed_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_per_call: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_data_classes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    max_context_utilization: Mapped[float] = mapped_column(Numeric(6, 5), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("cardinality(execution_classes) > 0", name="execution_classes_nonempty"),
        CheckConstraint("cardinality(allowed_roles) > 0", name="allowed_roles_nonempty"),
        CheckConstraint("cardinality(allowed_data_classes) > 0", name="data_classes_nonempty"),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("max_calls > 0", name="max_calls_positive"),
        CheckConstraint("max_input_tokens > 0", name="max_input_tokens_positive"),
        CheckConstraint("max_output_tokens > 0", name="max_output_tokens_positive"),
        CheckConstraint("max_cost_per_call >= 0", name="max_cost_nonnegative"),
        CheckConstraint("timeout_ms > 0", name="timeout_positive"),
        CheckConstraint(
            "max_context_utilization > 0 AND max_context_utilization < 1",
            name="context_utilization_range",
        ),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("revision > 0", name="revision_positive"),
        Index(
            "uq_workspace_model_binding_active",
            "workspace_id",
            "model_deployment_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )


class ModelPolicyRevision(CreatedAtMixin, Base):
    __tablename__ = "model_policy_revisions"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    eligible_binding_revisions: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    role_policies: Mapped[dict] = mapped_column(JSONB, nullable=False)
    budget_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context_policy_revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("context_policy_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    verifier_policy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "revision", name="uq_model_policy_revision"),
        CheckConstraint("cardinality(eligible_binding_revisions) > 0", name="bindings_nonempty"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )
