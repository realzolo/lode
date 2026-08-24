"""Application-scoped, externally managed read-only integrations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base


class ApplicationIntegration(Base):
    """A service connection that may only contribute read-only evidence.

    ``config`` holds non-secret selectors such as zone IDs, Kafka topics, or a
    ClickHouse database.  ``secret_ref`` is encrypted at rest and is never
    returned by public API projections.
    """

    __tablename__ = "application_integrations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    readonly_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('redis', 'kafka', 'clickhouse')", name="kind"
        ),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
    )
