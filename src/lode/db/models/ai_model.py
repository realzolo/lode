"""Global AI model configuration."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Text,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column
from lode.db.base import Base


class AiModelConfig(Base):
    __tablename__ = "ai_model_configs"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ref: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    last_test_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="untested"
    )
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    last_test_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('openai', 'anthropic')", name="provider"
        ),
        CheckConstraint(
            "last_test_status IN ('untested', 'available', 'unavailable')",
            name="last_test_status",
        ),
        # Exactly one global default.
        Index(
            "ux_ai_model_configs_default",
            "is_default",
            unique=True,
            postgresql_where="is_default",
        ),
    )
