"""EvidenceConnector and immutable access-scope models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, TimestampMixin, identity_pk


class EvidenceConnector(TimestampMixin, Base):
    __tablename__ = "evidence_connectors"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    kind_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    instance_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    verification_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="untested"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    last_introspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_evidence_connector_name"),
        CheckConstraint("kind_version > 0", name="kind_version_positive"),
        CheckConstraint("instance_revision > 0", name="instance_revision_positive"),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint(
            "verification_status IN ('untested', 'healthy', 'unavailable')",
            name="verification_status",
        ),
        CheckConstraint(
            "verification_status <> 'healthy' OR verified_at IS NOT NULL",
            name="healthy_has_verified_at",
        ),
        CheckConstraint("cardinality(capabilities) > 0", name="capabilities_nonempty"),
    )


class EvidenceAccessScope(CreatedAtMixin, Base):
    __tablename__ = "evidence_access_scopes"

    id: Mapped[int] = identity_pk()
    connector_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evidence_connectors.id", ondelete="CASCADE"), nullable=False
    )
    allowed_languages: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    scope_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_catalog: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_catalog_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    read_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_budget_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalization_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("connector_id", "revision", name="uq_evidence_access_scope_revision"),
        CheckConstraint("cardinality(allowed_languages) > 0", name="languages_nonempty"),
        CheckConstraint(
            "allowed_languages <@ ARRAY['logql','elasticsearch_query_dsl','opensearch_query_dsl',"
            "'sql','https','command']::text[]",
            name="languages_closed",
        ),
        CheckConstraint("schema_catalog_revision > 0", name="schema_revision_positive"),
        CheckConstraint("read_policy_revision > 0", name="policy_revision_positive"),
        CheckConstraint(
            "normalization_policy_revision > 0", name="normalization_revision_positive"
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
    )
