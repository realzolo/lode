"""Alert ingestion model (one row per consumed Kafka message)."""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    # Producer-supplied dedupe key, computed by lark-alert.ts buildAlertKey and
    # carried verbatim on the wire (no platform recompute — fixed to the v1 format).
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    level: Mapped[str] = mapped_column(Text, nullable=False)
    # Producer-assigned alert id — correlates this row with the Kafka alert event.
    alert_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the incident actually occurred, per the producer (ISO-8601 timestamp).
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Producer-side dedupe TTL in seconds (mirrors lark-alert.ts config).
    dedupe_ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Structured error log from the producer (lark-alert.ts AlertErrorLog), or null.
    error_log: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("level IN ('CRITICAL', 'WARNING')", name="level"),
        Index("ix_alerts_dedupe_key", "dedupe_key"),
        Index("ix_alerts_application_id", "application_id"),
        Index(
            "ix_alerts_fields",
            "fields",
            postgresql_using="gin",
            postgresql_ops={"fields": "jsonb_path_ops"},
        ),
    )
