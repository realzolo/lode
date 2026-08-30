"""Rebuild incident operations around occurrences and immutable investigation runs.

Revision ID: 0012_incident_platform_rebuild
Revises: 0011_clickhouse_sql_scope
Create Date: 2026-08-30 21:00:00.000000

This is intentionally destructive. The product has no historical incident-data
compatibility obligation: legacy alerts and investigation runs are discarded
before the operational Incident model is installed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_incident_platform_rebuild"
down_revision: str | None = "0011_clickhouse_sql_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A closed incident replaces the old per-investigation archive state. Drop
    # the archive trigger before removing its referenced columns.
    op.execute(sa.text("DROP FUNCTION IF EXISTS reject_archived_investigation_mutation() CASCADE"))

    # No historical compatibility path exists for this replacement.
    op.execute(sa.text("TRUNCATE TABLE investigations, incidents, alerts CASCADE"))
    op.execute(sa.text("DROP TABLE alerts CASCADE"))
    op.execute(sa.text("DROP TABLE incidents CASCADE"))

    op.execute(
        sa.text(
            "ALTER TABLE ingestion_events DROP CONSTRAINT IF EXISTS ck_ingestion_events_outcome"
        )
    )
    op.alter_column("ingestion_events", "alert_id", new_column_name="source_event_id")
    op.alter_column("ingestion_events", "alert_row_id", new_column_name="occurrence_id")
    op.execute(
        sa.text(
            "ALTER TABLE ingestion_events ADD CONSTRAINT ck_ingestion_events_outcome "
            "CHECK (outcome IN ('accepted', 'correlated', 'duplicate', 'dead_letter', 'unassigned'))"
        )
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger(), server_default=sa.text("next_lode_id()"), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="open", nullable=False),
        sa.Column("first_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("recurrence_of_id", sa.BigInteger(), nullable=True),
        sa.Column("assigned_to", sa.BigInteger(), nullable=True),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(dedup_key) > 0", name=op.f("ck_incidents_dedup_key_nonempty")),
        sa.CheckConstraint(
            "severity IN ('CRITICAL', 'WARNING')", name=op.f("ck_incidents_severity")
        ),
        sa.CheckConstraint(
            "state IN ('open', 'acknowledged', 'mitigated', 'resolved', 'closed')",
            name=op.f("ck_incidents_state"),
        ),
        sa.CheckConstraint(
            "occurrence_count > 0", name=op.f("ck_incidents_occurrence_count_positive")
        ),
        sa.CheckConstraint(
            "last_occurred_at >= first_occurred_at", name=op.f("ck_incidents_occurrence_range")
        ),
        sa.CheckConstraint("state_version > 0", name=op.f("ck_incidents_state_version_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_incidents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_of_id"],
            ["incidents.id"],
            name=op.f("fk_incidents_recurrence_of_id_incidents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name=op.f("fk_incidents_assigned_to_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
    )
    op.create_index(
        "uq_incident_active_dedup_key",
        "incidents",
        ["workspace_id", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'acknowledged', 'mitigated')"),
    )
    op.create_index(
        "ix_incidents_workspace_state_updated",
        "incidents",
        ["workspace_id", "state", "updated_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_incidents_updated_at BEFORE UPDATE ON incidents "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "incident_occurrences",
        sa.Column("id", sa.BigInteger(), server_default=sa.text("next_lode_id()"), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=True),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("trace_id_ciphertext", sa.Text(), nullable=True),
        sa.Column("trace_id_hash", sa.Text(), nullable=True),
        sa.Column("source_revision", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_payload_masked", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('kafka', 'manual')", name=op.f("ck_incident_occurrences_source_type")
        ),
        sa.CheckConstraint(
            "event_kind IN ('firing', 'recovered')", name=op.f("ck_incident_occurrences_event_kind")
        ),
        sa.CheckConstraint(
            "severity IN ('CRITICAL', 'WARNING')", name=op.f("ck_incident_occurrences_severity")
        ),
        sa.CheckConstraint(
            "length(dedup_key) > 0", name=op.f("ck_incident_occurrences_dedup_key_nonempty")
        ),
        sa.CheckConstraint(
            "source_event_id IS NOT NULL OR source_type = 'manual'",
            name=op.f("ck_incident_occurrences_source_event_id_required"),
        ),
        sa.CheckConstraint(
            "source_revision IS NULL OR source_revision ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_incident_occurrences_source_revision_sha"),
        ),
        sa.CheckConstraint(
            "trace_id_hash IS NULL OR trace_id_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_incident_occurrences_trace_id_hash_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_incident_occurrences_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_occurrences_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_occurrences")),
    )
    op.create_index(
        "uq_incident_occurrence_source_event",
        "incident_occurrences",
        ["workspace_id", "source_event_id"],
        unique=True,
        postgresql_where=sa.text("source_event_id IS NOT NULL"),
    )
    op.create_index(
        "ix_incident_occurrences_incident_occurred",
        "incident_occurrences",
        ["incident_id", "occurred_at"],
        unique=False,
    )

    op.drop_column("investigations", "alert_id")
    op.drop_column("investigations", "archived_by")
    op.drop_column("investigations", "archived_at")
    op.add_column(
        "investigations", sa.Column("trigger_occurrence_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "investigations",
        sa.Column("trigger_reason", sa.Text(), server_default="initial", nullable=False),
    )
    op.alter_column("investigations", "incident_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key(
        op.f("fk_investigations_incident_id_incidents"),
        "investigations",
        "incidents",
        ["incident_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_investigations_trigger_occurrence_id_incident_occurrences"),
        "investigations",
        "incident_occurrences",
        ["trigger_occurrence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "trigger_reason",
        "investigations",
        "trigger_reason IN ('initial', 'severity_escalation', 'evidence_change', 'operator_request', 'retry')",
    )
    op.create_index(
        "ix_investigations_trigger_occurrence",
        "investigations",
        ["trigger_occurrence_id"],
        unique=False,
    )

    op.create_table(
        "incident_events",
        sa.Column("id", sa.BigInteger(), server_default=sa.text("next_lode_id()"), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('opened', 'occurrence_added', 'state_changed', 'assigned', "
            "'investigation_started', 'review_recorded', 'action_created', 'action_updated')",
            name=op.f("ck_incident_events_event_type"),
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_events_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_incident_events_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_events")),
    )
    op.create_index(
        "ix_incident_events_incident_created",
        "incident_events",
        ["incident_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "incident_actions",
        sa.Column("id", sa.BigInteger(), server_default=sa.text("next_lode_id()"), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("investigation_id", sa.BigInteger(), nullable=True),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="proposed", nullable=False),
        sa.Column("priority", sa.Text(), server_default="P2", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("validation", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_type IN ('mitigate', 'remediate', 'validate', 'prevent')",
            name=op.f("ck_incident_actions_action_type"),
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'in_progress', 'verified', 'rejected', 'cancelled')",
            name=op.f("ck_incident_actions_status"),
        ),
        sa.CheckConstraint(
            "priority IN ('P0', 'P1', 'P2', 'P3')", name=op.f("ck_incident_actions_priority")
        ),
        sa.CheckConstraint("length(title) > 0", name=op.f("ck_incident_actions_title_nonempty")),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_actions_incident_id_incidents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_incident_actions_investigation_id_investigations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_incident_actions_owner_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_incident_actions_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_actions")),
    )
    op.create_index(
        "ix_incident_actions_incident_status",
        "incident_actions",
        ["incident_id", "status", "updated_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_incident_actions_updated_at BEFORE UPDATE ON incident_actions "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "investigation_reviews",
        sa.Column("id", sa.BigInteger(), server_default=sa.text("next_lode_id()"), nullable=False),
        sa.Column("investigation_id", sa.BigInteger(), nullable=False),
        sa.Column("code_finding_id", sa.BigInteger(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IN ('accepted', 'rejected', 'needs_evidence')",
            name=op.f("ck_investigation_reviews_verdict"),
        ),
        sa.CheckConstraint(
            "length(comment) > 0", name=op.f("ck_investigation_reviews_comment_nonempty")
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id"],
            ["investigations.id"],
            name=op.f("fk_investigation_reviews_investigation_id_investigations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["code_finding_id"],
            ["investigation_code_findings.id"],
            name=op.f("fk_investigation_reviews_code_finding_id_investigation_code_findings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_investigation_reviews_reviewer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investigation_reviews")),
    )
    op.create_index(
        "ix_investigation_reviews_investigation_created",
        "investigation_reviews",
        ["investigation_id", "created_at"],
        unique=False,
    )
    for table_name in ("incident_occurrences", "incident_events", "investigation_reviews"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_mutation()"
            )
        )


def downgrade() -> None:
    raise RuntimeError("The incident platform rebuild has no compatibility downgrade path.")
