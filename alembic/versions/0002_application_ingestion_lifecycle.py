"""application ingestion lifecycle

Revision ID: 0002_application_ingestion_lifecycle
Revises: 0001_initial
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0002_app_ingestion_lifecycle"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        "ALTER TABLE applications ADD COLUMN ingestion_state text NOT NULL DEFAULT 'draft';",
        "ALTER TABLE applications ADD COLUMN ingestion_version integer NOT NULL DEFAULT 0;",
        "ALTER TABLE applications ADD COLUMN ingestion_start_position text;",
        "ALTER TABLE applications ADD COLUMN ingestion_started_at timestamptz;",
        "ALTER TABLE applications ADD COLUMN ingestion_paused_at timestamptz;",
        "ALTER TABLE applications ADD CONSTRAINT ck_applications_ingestion_state CHECK (ingestion_state IN ('draft', 'active', 'paused'));",
        "ALTER TABLE applications ADD CONSTRAINT ck_applications_ingestion_start_position CHECK (ingestion_start_position IS NULL OR ingestion_start_position IN ('earliest', 'latest'));",
        "UPDATE applications SET ingestion_state = 'paused' WHERE id IN (SELECT application_id FROM application_kafka);",
        """
        CREATE TABLE application_ingestion_runtime (
            application_id bigint PRIMARY KEY REFERENCES applications(id) ON DELETE CASCADE,
            observed_state text NOT NULL DEFAULT 'idle',
            observed_version integer NOT NULL DEFAULT 0,
            consumer_id text,
            assigned_partitions integer NOT NULL DEFAULT 0,
            backlog bigint,
            last_heartbeat_at timestamptz,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_application_ingestion_runtime_observed_state
                CHECK (observed_state IN ('idle', 'starting', 'listening', 'paused', 'error'))
        );
        """,
        """
        CREATE TABLE application_ingestion_offsets (
            application_id bigint NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            ingestion_version integer NOT NULL,
            topic text NOT NULL,
            partition integer NOT NULL,
            start_position text NOT NULL,
            target_offset bigint NOT NULL,
            initialized_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_application_ingestion_offsets
                PRIMARY KEY (application_id, ingestion_version, topic, partition),
            CONSTRAINT ck_application_ingestion_offsets_start_position
                CHECK (start_position IN ('earliest', 'latest'))
        );
        """,
        "CREATE INDEX ix_application_ingestion_offsets_topic ON application_ingestion_offsets (topic, partition);",
        """
        CREATE TRIGGER trg_application_ingestion_runtime_updated_at
        BEFORE UPDATE ON application_ingestion_runtime
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """,
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    statements = [
        "DROP TABLE IF EXISTS application_ingestion_offsets;",
        "DROP TABLE IF EXISTS application_ingestion_runtime;",
        "ALTER TABLE applications DROP CONSTRAINT IF EXISTS ck_applications_ingestion_start_position;",
        "ALTER TABLE applications DROP CONSTRAINT IF EXISTS ck_applications_ingestion_state;",
        "ALTER TABLE applications DROP COLUMN IF EXISTS ingestion_paused_at;",
        "ALTER TABLE applications DROP COLUMN IF EXISTS ingestion_started_at;",
        "ALTER TABLE applications DROP COLUMN IF EXISTS ingestion_start_position;",
        "ALTER TABLE applications DROP COLUMN IF EXISTS ingestion_version;",
        "ALTER TABLE applications DROP COLUMN IF EXISTS ingestion_state;",
    ]
    for statement in statements:
        op.execute(text(statement))
