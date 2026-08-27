"""Local user accounts.

The product has one immutable system administrator and ordinary Workbench
users.  Invitations and delegated global roles are intentionally not part of
the schema.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Text,
    ForeignKey,
    Index,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    must_change_password: Mapped[bool] = mapped_column(
        nullable=False, server_default=sql_text("false")
    )
    is_system_admin: Mapped[bool] = mapped_column(
        nullable=False, server_default=sql_text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
        CheckConstraint("username = lower(btrim(username))", name="username_normalized"),
        CheckConstraint(
            "username ~ '^[a-z][a-z0-9._-]{2,31}$'", name="username_format"
        ),
        Index(
            "uq_users_system_admin",
            "is_system_admin",
            unique=True,
            postgresql_where=sql_text("is_system_admin"),
        ),
    )
