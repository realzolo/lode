"""Application (isolation unit) and its configuration models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ingestion_topic: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    model_config_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_model_configs.id", ondelete="SET NULL")
    )
    # Desired control-plane state. Runtime health is recorded separately because
    # an active application is not necessarily assigned to a live consumer yet.
    ingestion_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="draft"
    )
    ingestion_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    ingestion_start_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingestion_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ingestion_paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint("ingestion_state IN ('draft', 'active', 'paused')", name="ingestion_state"),
        CheckConstraint(
            "ingestion_start_position IS NULL OR ingestion_start_position IN ('earliest', 'latest')",
            name="ingestion_start_position",
        ),
    )


class Service(Base):
    """Globally unique runtime service mapped to exactly one source repository."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    service_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    repo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_repos.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
    )


class ApplicationServiceBinding(Base):
    """Many-to-many application access boundary for shared services."""

    __tablename__ = "application_service_bindings"

    application_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint("role IN ('primary', 'shared')", name="role"),
        Index(
            "uq_application_service_primary",
            "application_id",
            unique=True,
            postgresql_where=sql_text("role = 'primary'"),
        ),
    )


class ApplicationIngestionRuntime(Base):
    """Consumer-observed state for an application's current ingestion version."""

    __tablename__ = "application_ingestion_runtime"

    application_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    observed_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="idle"
    )
    observed_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    consumer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_partitions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    backlog: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "observed_state IN ('idle', 'starting', 'listening', 'paused', 'error')",
            name="observed_state",
        ),
    )


class ApplicationIngestionOffset(Base):
    """Durable first-start cursor for one assigned Kafka partition.

    Persisting the selected offset before committing it to Kafka makes first
    activation retryable without changing a "latest" starting point.
    """

    __tablename__ = "application_ingestion_offsets"

    application_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ingestion_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(Text, primary_key=True)
    partition: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_position: Mapped[str] = mapped_column(Text, nullable=False)
    target_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    initialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "start_position IN ('earliest', 'latest')", name="start_position"
        ),
        Index("ix_application_ingestion_offsets_topic", "topic", "partition"),
    )


class ApplicationRepo(Base):
    __tablename__ = "application_repos"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    repo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_repos.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        UniqueConstraint("application_id", "repo_id", name="uq_app_repo"),
    )


class ApplicationArchitectureContext(Base):
    __tablename__ = "application_architecture_contexts"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
