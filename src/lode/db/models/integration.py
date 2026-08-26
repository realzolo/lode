"""Application-owned, capability-limited integration instances."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class ApplicationIntegration(Base):
    """A service connection that may only contribute read-only evidence.

    ``kind`` is deliberately free text. Runtime support is controlled by the
    integration-kind registry, so adding a kind never requires a schema change.
    Non-secret configuration and encrypted secret JSON are stored separately.
    """

    __tablename__ = "application_integrations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    kind_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    secrets_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="verified"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )

    __table_args__ = (
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint(
            "verification_status IN ('verified', 'failed')",
            name="verification_status",
        ),
        CheckConstraint("kind_version > 0", name="kind_version_positive"),
        CheckConstraint("revision > 0", name="revision_positive"),
        UniqueConstraint("application_id", "name", name="uq_application_integration_name"),
        Index(
            "ix_application_integrations_application_state",
            "application_id",
            "state",
        ),
    )
