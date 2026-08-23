"""Shared memory model (reusable conclusions per application)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy import ARRAY, Float

from lode.db.base import Base
from lode.db.vector import EMBEDDING_DIM


class Memory(Base):
    __tablename__ = "memories"

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
    # memories recorded before embedding was enabled, or when no embedding
    # provider is configured. Used for cosine-similarity retrieval in
    # ``get_memory`` (semantic memory). Dimension is fixed at EMBEDDING_DIM.
    embedding: Mapped[list[float] | None] = mapped_column(
        ARRAY(Float), nullable=True
    )
    source_analysis_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("analyses.id", ondelete="SET NULL")
    )
    is_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        Index("ix_memories_application_id", "application_id"),
        Index("ix_memories_trigger_signature", "trigger_signature"),
    )
