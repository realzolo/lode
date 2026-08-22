"""AI model configuration (global default or per-application override)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class AiModelConfig(Base):
    __tablename__ = "ai_model_configs"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="global")
    application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ref: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("scope IN ('global', 'application')", name="scope"),
        CheckConstraint(
            "provider IN ('openai', 'anthropic')", name="provider"
        ),
        CheckConstraint(
            "scope = 'application' OR application_id IS NULL",
            name="scope_application",
        ),
        # Exactly one default per scope (global, or per application).
        Index(
            "ux_ai_model_configs_global_default",
            "scope",
            unique=True,
            postgresql_where="scope = 'global' AND is_default",
        ),
        Index(
            "ux_ai_model_configs_app_default",
            "application_id",
            unique=True,
            postgresql_where="scope = 'application' AND is_default",
        ),
    )
