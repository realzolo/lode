"""add application read-only integrations and service evidence

Revision ID: 0006_readonly_integrations
Revises: 0005_platform_ai_output_language
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0006_readonly_integrations"
down_revision: str | Sequence[str] | None = "0005_platform_ai_output_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("""
        CREATE TABLE application_integrations (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            name text NOT NULL,
            kind text NOT NULL,
            config jsonb NOT NULL DEFAULT '{}'::jsonb,
            secret_ref text NOT NULL,
            state text NOT NULL DEFAULT 'active',
            readonly_verified_at timestamptz,
            last_collected_at timestamptz,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_application_integrations_kind
                CHECK (kind IN ('redis', 'kafka', 'clickhouse')),
            CONSTRAINT ck_application_integrations_state
                CHECK (state IN ('active', 'disabled'))
        )
    """))
    op.execute(text("""
        CREATE INDEX ix_application_integrations_application_id
            ON application_integrations(application_id)
    """))
    op.execute(text("""
        CREATE TRIGGER trg_application_integrations_updated_at
        BEFORE UPDATE ON application_integrations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """))
    op.execute(text("""
        ALTER TABLE evidence_artifacts DROP CONSTRAINT ck_evidence_artifacts_type
    """))
    op.execute(text("""
        ALTER TABLE evidence_artifacts ADD CONSTRAINT ck_evidence_artifacts_type
            CHECK (artifact_type IN ('git_file', 'git_diff', 'db_query', 'deploy',
                                    'alert_payload', 'service_snapshot'))
    """))
    op.execute(text("""
        ALTER TABLE analysis_steps DROP CONSTRAINT ck_analysis_steps_node_type
    """))
    op.execute(text("""
        ALTER TABLE analysis_steps ADD CONSTRAINT ck_analysis_steps_node_type
            CHECK (node_type IN ('receive', 'git_sync', 'context', 'service_snapshot',
                                'ai_analysis', 'experience', 'conclusion'))
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE analysis_steps DROP CONSTRAINT ck_analysis_steps_node_type"))
    op.execute(text("""
        ALTER TABLE analysis_steps ADD CONSTRAINT ck_analysis_steps_node_type
            CHECK (node_type IN ('receive', 'git_sync', 'context', 'ai_analysis',
                                'experience', 'conclusion'))
    """))
    op.execute(text("ALTER TABLE evidence_artifacts DROP CONSTRAINT ck_evidence_artifacts_type"))
    op.execute(text("""
        ALTER TABLE evidence_artifacts ADD CONSTRAINT ck_evidence_artifacts_type
            CHECK (artifact_type IN ('git_file', 'git_diff', 'db_query', 'deploy',
                                    'alert_payload'))
    """))
    op.execute(text("DROP TABLE IF EXISTS application_integrations"))
