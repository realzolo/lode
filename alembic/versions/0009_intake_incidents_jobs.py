"""add intake, incidents, jobs, evidence and audit tables

Revision ID: 0009_intake_jobs
Revises: 0008_memory_ttl
Create Date: 2026-08-23

Production intake/execution contract: Kafka delivery becomes idempotent via
``ingestion_events``, repeated alerts collapse into ``incidents``, and analysis
work is durable and claimable via ``analysis_jobs`` (one active job per incident
through a partial unique index). ``evidence_artifacts`` and ``audit_events`` are
the citable-proof and append-only control-plane records. ``analyses`` gains
incident/job linkage and failure attribution.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_intake_jobs"
down_revision: str | Sequence[str] | None = "0008_memory_ttl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- analyses: link to incident/job + failure attribution ----------------
    op.add_column(
        "analyses",
        sa.Column("incident_id", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "analyses",
        sa.Column("job_id", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "analyses", sa.Column("engine_version", sa.Text, nullable=True)
    )
    op.add_column(
        "analyses", sa.Column("failure_code", sa.Text, nullable=True)
    )
    op.add_column(
        "analyses", sa.Column("failure_detail", sa.Text, nullable=True)
    )
    op.create_index("ix_analyses_incident_id", "analyses", ["incident_id"])
    op.create_foreign_key(
        "fk_analyses_incident_id_incidents",
        "analyses",
        "incidents",
        ["incident_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_analyses_job_id_analysis_jobs",
        "analyses",
        "analysis_jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )

    # --- ingestion_events (Kafka idempotency) --------------------------------
    op.create_table(
        "ingestion_events",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("application_id", sa.BigInteger, nullable=True),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("partition", sa.Integer, nullable=True),
        sa.Column("offset", sa.BigInteger, nullable=True),
        sa.Column("producer_event_id", sa.Text, nullable=True),
        sa.Column("payload_hash", sa.Text, nullable=True),
        sa.Column("trace_id", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="accepted"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'dlq', 'unassigned')", name="ck_ingestion_events_status"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "uq_ingestion_events_topic_partition_offset",
        "ingestion_events",
        ["topic", "partition", "offset"],
        unique=True,
    )

    # --- incidents -----------------------------------------------------------
    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("public_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("application_id", sa.BigInteger, nullable=False),
        sa.Column("dedupe_key", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="open"),
        sa.Column("first_alert_id", sa.BigInteger, nullable=True),
        sa.Column("latest_alert_id", sa.BigInteger, nullable=True),
        sa.Column("alert_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "state IN ('open', 'resolved', 'suppressed')", name="ck_incidents_state"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["first_alert_id"], ["alerts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["latest_alert_id"], ["alerts.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("uq_incidents_public_id", "incidents", ["public_id"], unique=True)
    op.create_index(
        "uq_incidents_application_id_dedupe_key",
        "incidents",
        ["application_id", "dedupe_key"],
        unique=True,
    )

    # --- analysis_jobs -------------------------------------------------------
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("public_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("incident_id", sa.BigInteger, nullable=False),
        sa.Column("analysis_id", sa.BigInteger, nullable=True),
        sa.Column("trigger", sa.Text, nullable=False, server_default="ingest"),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_owner", sa.Text, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text, nullable=True),
        sa.Column("last_error_detail", sa.Text, nullable=True),
        sa.Column("requested_by", sa.BigInteger, nullable=True),
        sa.Column("trace_id", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', "
            "'failed', 'canceled', 'dead')",
            name="ck_analysis_jobs_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_analysis_jobs_attempt"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["analyses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("uq_analysis_jobs_public_id", "analysis_jobs", ["public_id"], unique=True)
    op.create_index(
        "ix_analysis_jobs_status_available",
        "analysis_jobs",
        ["status", "available_at", "priority", "created_at"],
    )
    op.create_index(
        "ix_analysis_jobs_lease_expires",
        "analysis_jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_analysis_jobs_active_incident",
        "analysis_jobs",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'running', 'retry_wait')"
        ),
    )

    # --- evidence_artifacts --------------------------------------------------
    op.create_table(
        "evidence_artifacts",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("analysis_id", sa.BigInteger, nullable=False),
        sa.Column("artifact_type", sa.Text, nullable=False),
        sa.Column("source_kind", sa.Text, nullable=True),
        sa.Column("source_id", sa.BigInteger, nullable=True),
        sa.Column("locator", sa.Text, nullable=True),
        sa.Column("content_hash", sa.Text, nullable=True),
        sa.Column("redacted_excerpt", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "artifact_type IN ('git_file', 'git_diff', 'db_query', "
            "'deploy', 'alert_payload')",
            name="ck_evidence_artifacts_type",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["analyses.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_evidence_artifacts_analysis_id", "evidence_artifacts", ["analysis_id"]
    )

    # --- audit_events --------------------------------------------------------
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("actor_id", sa.BigInteger, nullable=True),
        sa.Column("actor_email", sa.Text, nullable=True),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=True),
        sa.Column("target_id", sa.Text, nullable=True),
        sa.Column("application_id", sa.BigInteger, nullable=True),
        sa.Column("request_id", sa.Text, nullable=True),
        sa.Column("trace_id", sa.Text, nullable=True),
        sa.Column("result", sa.Text, nullable=False, server_default="ok"),
        sa.Column("detail", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("evidence_artifacts")
    op.drop_index("uq_analysis_jobs_active_incident", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_lease_expires", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_status_available", table_name="analysis_jobs")
    op.drop_index("uq_analysis_jobs_public_id", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")
    op.drop_index(
        "uq_incidents_application_id_dedupe_key", table_name="incidents"
    )
    op.drop_index("uq_incidents_public_id", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index(
        "uq_ingestion_events_topic_partition_offset", table_name="ingestion_events"
    )
    op.drop_table("ingestion_events")
    op.drop_constraint(
        "fk_analyses_job_id_analysis_jobs", "analyses", type_="foreignkey",
        use_alter=True,
    )
    op.drop_constraint(
        "fk_analyses_incident_id_incidents", "analyses", type_="foreignkey",
        use_alter=True,
    )
    op.drop_index("ix_analyses_incident_id", table_name="analyses")
    op.drop_column("analyses", "failure_detail")
    op.drop_column("analyses", "failure_code")
    op.drop_column("analyses", "engine_version")
    op.drop_column("analyses", "job_id")
    op.drop_column("analyses", "incident_id")
