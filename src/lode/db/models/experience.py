"""Shared experience model (reusable conclusions per application)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import REAL
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class Experience(Base):
    __tablename__ = "experiences"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    trigger_signature: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Semantic embedding of the triggering incident signature, stored as a
    # native PostgreSQL ``real[]`` (no pgvector extension required). NULL for
    # experiences recorded before embedding was enabled, or when no embedding
    # provider is configured. Used for cosine-similarity retrieval in
    # ``get_experience`` (semantic experience). Dimension is fixed at EMBEDDING_DIM.
    embedding: Mapped[list[float] | None] = mapped_column(
        ARRAY(REAL), nullable=True
    )
    source_analysis_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("analyses.id", ondelete="SET NULL")
    )
    is_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Time-to-live for a reusable conclusion. NULL means "never expires".
    # When set, the experience is treated as stale (not returned by get_experience and
    # reaped by the startup reaper) once ``expires_at`` passes — so a conclusion
    # from a long-resolved incident does not keep shadowing fresh analyses
    # forever. Set from ``settings.experience_ttl_days`` on write.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index("ix_experiences_application_id", "application_id"),
        Index("ix_experiences_trigger_signature", "trigger_signature"),
        Index("ix_experiences_expires_at", "expires_at"),
    )

    @staticmethod
    def ttl_expiry(ttl_days: int) -> datetime | None:
        """Compute ``expires_at`` for a experience written now with the given TTL.

        Returns ``None`` when ``ttl_days <= 0`` (no expiry).
        """
        if ttl_days <= 0:
            return None
        return datetime.now(UTC) + timedelta(days=ttl_days)
