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
    Index,
    Text,
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class GitCredential(Base):
    __tablename__ = "git_credentials"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    auth_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="ssh")
    username: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    readonly: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
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
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="main"
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="global")
    application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE")
    )
    # Provider family (github / gitlab / gitee / bitbucket / other). Kept as a
    # free-text tag so new hosts can be onboarded without a schema change; the
    # settings UI offers a curated dropdown of the common ones.
    repo_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="other"
    )
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("git_credentials.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint("scope IN ('global', 'application')", name="scope"),
        CheckConstraint(
            "(scope = 'global' AND application_id IS NULL) OR "
            "(scope = 'application' AND application_id IS NOT NULL)",
            name="scope_application",
        ),
        Index(
            "ux_git_repos_global_repo_url",
            "repo_url",
            unique=True,
            postgresql_where="scope = 'global'",
        ),
        Index(
            "ux_git_repos_app_repo_url",
            "application_id",
            "repo_url",
            unique=True,
            postgresql_where="scope = 'application'",
        ),
    )
