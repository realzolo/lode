"""Git credentials (global read-only account) and git repository registry."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from incident_trace.db.base import Base


class GitCredential(Base):
    __tablename__ = "git_credentials"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    auth_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="ssh")
    username: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    readonly: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("auth_type IN ('ssh', 'https')", name="auth_type"),
    )


class GitRepo(Base):
    __tablename__ = "git_repos"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    repo_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    default_branch: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="main"
    )
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("git_credentials.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
