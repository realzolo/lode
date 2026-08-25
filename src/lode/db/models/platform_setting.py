"""Persisted platform-wide settings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class PlatformSetting(Base):
    """One keyed value shared by every application and analysis task."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "key <> 'ai_output_language' OR value IN ('en', 'zh')",
            name="ai_output_language",
        ),
    )
