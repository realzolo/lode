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
    # Recomputed by the platform using the exact lark-alert.ts algorithm.
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    level: Mapped[str] = mapped_column(Text, nullable=False)
    env: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
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
