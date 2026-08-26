"""Immutable evidence store and investigation-local evidence graph."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, identity_pk


class EvidenceCollection(CreatedAtMixin, Base):
    __tablename__ = "evidence_collections"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_operations.id", ondelete="SET NULL")
    )
    connector_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_connector_snapshots.id", ondelete="RESTRICT")
    )
    collection_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    selector_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    artifact_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    result_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "collection_kind IN ('input', 'source', 'native_read', 'snapshot', 'operator')",
            name="collection_kind",
        ),
        CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed', 'rejected')", name="status"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_sha256"),
        CheckConstraint("artifact_count >= 0 AND result_bytes >= 0", name="result_counts_nonnegative"),
        CheckConstraint("finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at", name="collection_range"),
        UniqueConstraint("investigation_id", "fingerprint", name="uq_evidence_collection_fingerprint"),
    )


class EvidenceArtifact(CreatedAtMixin, Base):
    __tablename__ = "evidence_artifacts"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    collection_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("evidence_collections.id", ondelete="SET NULL")
    )
    artifact_kind: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_class: Mapped[str] = mapped_column(Text, nullable=False)
    content_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_time_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_time_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_revision: Mapped[str | None] = mapped_column(Text)
    data_class: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_injection_markers: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256"),
        CheckConstraint(
            "source_time_end IS NULL OR source_time_start IS NULL OR source_time_end >= source_time_start",
            name="source_time_range",
        ),
        Index("ix_evidence_artifacts_run_kind", "investigation_id", "artifact_kind"),
        Index("ix_evidence_artifacts_content_hash", "content_hash"),
    )


class EvidenceLink(CreatedAtMixin, Base):
    __tablename__ = "evidence_links"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evidence_artifacts.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('operation', 'entity', 'event', 'relation', 'assertion', "
            "'source_assessment', 'code_finding', 'report')",
            name="source_type",
        ),
        CheckConstraint(
            "relation IN ('supports', 'contradicts', 'derived_from', 'observed_in', 'validates')",
            name="relation",
        ),
        UniqueConstraint("source_type", "source_id", "artifact_id", "relation", name="uq_evidence_link"),
        Index("ix_evidence_links_artifact", "artifact_id"),
    )


class ObservedEntity(CreatedAtMixin, Base):
    __tablename__ = "observed_entities"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    component_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("investigation_component_snapshots.id", ondelete="SET NULL")
    )
    identity_status: Mapped[str] = mapped_column(Text, nullable=False)
    provider_identity_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attributes_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "entity_kind IN ('component', 'unknown_component', 'external_system', 'request', "
            "'job', 'transaction', 'topic', 'database')",
            name="entity_kind",
        ),
        CheckConstraint("identity_status IN ('verified', 'provisional', 'ambiguous', 'unknown')", name="identity_status"),
        UniqueConstraint("investigation_id", "stable_key", name="uq_observed_entity_stable_key"),
    )


class ObservedEvent(CreatedAtMixin, Base):
    __tablename__ = "observed_events"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    connector_snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigation_connector_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    provider_position: Mapped[str] = mapped_column(Text, nullable=False)
    raw_excerpt_masked: Mapped[str] = mapped_column(Text, nullable=False)
    attributes_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resource_attributes_masked: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace_match: Mapped[dict] = mapped_column(JSONB, nullable=False)
    component_candidates: Mapped[list] = mapped_column(JSONB, nullable=False)
    relation_hints: Mapped[list] = mapped_column(JSONB, nullable=False)
    revision_hints: Mapped[list] = mapped_column(JSONB, nullable=False)
    provider_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_artifact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("evidence_artifacts.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "connector_snapshot_id", "provider_position",
            name="uq_observed_event_provider_position",
        ),
        Index(
            "ix_observed_events_timeline",
            "investigation_id", "occurred_at", "connector_snapshot_id", "provider_position",
        ),
    )


class ObservedRelation(CreatedAtMixin, Base):
    __tablename__ = "observed_relations"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    source_entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("observed_entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("observed_entities.id", ondelete="CASCADE"), nullable=False
    )
    relation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    relation_basis: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("source_entity_id <> target_entity_id", name="distinct_endpoints"),
        CheckConstraint(
            "relation_kind IN ('participated_in', 'called', 'published_to', 'consumed_from', "
            "'depends_on', 'caused_by', 'supports', 'contradicts', 'same_identity_candidate')",
            name="relation_kind",
        ),
        CheckConstraint("status IN ('observed', 'hypothesis', 'confirmed', 'contradicted')", name="status"),
        CheckConstraint(
            "relation_kind NOT IN ('called', 'published_to', 'consumed_from', 'caused_by') "
            "OR cardinality(evidence_refs) > 0",
            name="causal_evidence_required",
        ),
        UniqueConstraint(
            "investigation_id", "source_entity_id", "target_entity_id", "relation_kind",
            name="uq_observed_relation",
        ),
    )


class EvidenceAssertion(CreatedAtMixin, Base):
    __tablename__ = "evidence_assertions"

    id: Mapped[int] = identity_pk()
    investigation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    assertion_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    structured_claim: Mapped[dict] = mapped_column(JSONB, nullable=False)
    supporting_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    counter_evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    missing_validation: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    assertion_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "assertion_kind IN ('fact', 'hypothesis', 'counter_evidence', 'gap', 'conclusion')",
            name="assertion_kind",
        ),
        CheckConstraint(
            "status IN ('proposed', 'supported', 'confirmed', 'contradicted', 'unresolved')",
            name="status",
        ),
        CheckConstraint("assertion_hash ~ '^[0-9a-f]{64}$'", name="assertion_hash_sha256"),
        UniqueConstraint("investigation_id", "assertion_hash", name="uq_evidence_assertion_hash"),
    )
