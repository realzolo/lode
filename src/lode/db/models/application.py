"""Application (isolation unit) and its configuration models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
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
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class ApplicationKafka(Base):
    __tablename__ = "application_kafka"

    application_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    topic: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
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
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
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
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "start_position IN ('earliest', 'latest')", name="start_position"
        ),
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
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        UniqueConstraint("application_id", "repo_id", name="uq_app_repo"),
    )


class ApplicationDescription(Base):
    __tablename__ = "application_descriptions"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    description_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="deploy"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "description_type IN ('deploy', 'other')",
            name="description_type",
        ),
    )


class DbSource(Base):
    __tablename__ = "db_sources"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Connection can be supplied two ways (mutually exclusive in practice):
    #  * structured fields below (host/port/database/username/password) — the
    #    DSN is built at query time; OR
    #  * conn_secret_ref — an env:// / vault:// / bare-literal reference resolved
    #    at query time so real credentials never have to live in this row.
    # At least one of the two must be provided (enforced in the schema layer).
    conn_secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    host: Mapped[str | None] = mapped_column(Text, nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TLS mode for structured connections. ``None`` leaves libpq's default
    # (prefer); ``require`` / ``verify-full`` force an encrypted link so a
    # cross-network connection to a production replica can't downgrade to
    # cleartext. Only meaningful for structured (host-based) sources.
    sslmode: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_tables: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # Per-source extra column names to mask on top of the built-in heuristic
    # hints. Lets an operator desensitize application-specific PII columns that
    # the generic name-matcher would otherwise let through.
    sensitive_columns: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
