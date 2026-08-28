"""Workspace, ingestion, permission, and control-plane audit models."""

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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, TimestampMixin, snowflake_pk


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = snowflake_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ingestion_topic: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    model_policy_revision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "model_policy_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_workspaces_model_policy_revision_id_model_policy_revisions",
        ),
    )
    architecture_context_revision_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "workspace_architecture_context_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_workspace_architecture_context",
        ),
    )
    ingestion_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    ingestion_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ingestion_start_position: Mapped[str | None] = mapped_column(Text)
    ingestion_activation_kind: Mapped[str | None] = mapped_column(Text)
    ingestion_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="name_nonblank"),
        CheckConstraint("char_length(description) <= 1000", name="description_length"),
        CheckConstraint("btrim(ingestion_topic) <> ''", name="topic_nonblank"),
        CheckConstraint("ingestion_state IN ('draft', 'active', 'paused')", name="ingestion_state"),
        CheckConstraint("ingestion_version >= 0", name="ingestion_version_nonnegative"),
        CheckConstraint(
            "ingestion_start_position IS NULL OR ingestion_start_position IN ('earliest', 'latest')",
            name="ingestion_start_position",
        ),
        CheckConstraint(
            "ingestion_activation_kind IS NULL OR ingestion_activation_kind IN ('start', 'resume')",
            name="ingestion_activation_kind",
        ),
        CheckConstraint(
            "(ingestion_state = 'draft' AND ingestion_start_position IS NULL "
            "AND ingestion_activation_kind IS NULL AND ingestion_started_at IS NULL "
            "AND ingestion_paused_at IS NULL) OR "
            "(ingestion_state = 'active' AND ingestion_version > 0 "
            "AND ingestion_start_position IS NOT NULL "
            "AND ingestion_activation_kind IS NOT NULL "
            "AND ingestion_started_at IS NOT NULL AND ingestion_paused_at IS NULL) OR "
            "(ingestion_state = 'paused' AND ingestion_version > 0 "
            "AND ingestion_start_position IS NOT NULL "
            "AND ingestion_activation_kind IS NOT NULL "
            "AND ingestion_started_at IS NOT NULL AND ingestion_paused_at IS NOT NULL)",
            name="ingestion_state_shape",
        ),
    )


class PlatformSettings(TimestampMixin, Base):
    """The single mutable platform-wide product setting object."""

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    ai_output_language: Mapped[str] = mapped_column(Text, nullable=False, server_default="en")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("ai_output_language IN ('en', 'zh')", name="output_language"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class WorkspaceArchitectureContextRevision(CreatedAtMixin, Base):
    """Immutable, structured Workspace background supplied to models as untrusted context."""

    __tablename__ = "workspace_architecture_context_revisions"

    id: Mapped[int] = snowflake_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    entries: Mapped[list] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "revision", name="uq_workspace_architecture_context_revision"),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("jsonb_typeof(entries) = 'array'", name="entries_array"),
    )


class WorkspaceIngestionRuntime(TimestampMixin, Base):
    __tablename__ = "workspace_ingestion_runtime"

    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    observed_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="idle")
    observed_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    consumer_id: Mapped[str | None] = mapped_column(Text)
    assigned_partitions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    backlog: Mapped[int | None] = mapped_column(BigInteger)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "observed_state IN ('idle', 'starting', 'listening', 'paused', 'error')",
            name="observed_state",
        ),
        CheckConstraint("observed_version >= 0", name="observed_version_nonnegative"),
        CheckConstraint("assigned_partitions >= 0", name="assigned_partitions_nonnegative"),
    )


class WorkspaceIngestionOffset(CreatedAtMixin, Base):
    __tablename__ = "workspace_ingestion_offsets"

    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    ingestion_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(Text, primary_key=True)
    partition: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_position: Mapped[str] = mapped_column(Text, nullable=False)
    target_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    initialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("ingestion_version > 0", name="ingestion_version_positive"),
        CheckConstraint("partition >= 0", name="partition_nonnegative"),
        CheckConstraint("target_offset >= 0", name="target_offset_nonnegative"),
        CheckConstraint("start_position IN ('earliest', 'latest')", name="start_position"),
        Index("ix_workspace_ingestion_offsets_topic", "topic", "partition"),
    )


class WorkspacePermission(CreatedAtMixin, Base):
    __tablename__ = "workspace_permissions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("permission IN ('viewer', 'operator')", name="permission"),
        Index("ix_workspace_permissions_workspace", "workspace_id", "permission"),
    )


class AuditEvent(CreatedAtMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = snowflake_pk()
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_username: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    workspace_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="RESTRICT")
    )
    http_request_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    result: Mapped[str] = mapped_column(Text, nullable=False, server_default="ok")
    detail: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("result IN ('ok', 'denied', 'failed')", name="result"),
        Index("ix_audit_events_actor_id", "actor_id"),
        Index("ix_audit_events_workspace_id", "workspace_id"),
        Index("ix_audit_events_created_at", "created_at"),
    )
