"""User-to-application permission assignment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class UserApplicationPerm(Base):
    __tablename__ = "user_application_perms"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    application_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    perm: Mapped[str] = mapped_column(Text, nullable=False, server_default="read")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint("perm IN ('read', 'analyze', 'admin')", name="perm"),
    )
