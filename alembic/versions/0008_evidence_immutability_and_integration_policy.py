"""enforce immutable evidence and secret-free integration selectors

Revision ID: 0008_evidence_immutability_and_integration_policy
Revises: 0007_integration_updated_at_trigger
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0008_evidence_immutability"
down_revision: str | Sequence[str] | None = "0007_integration_updated"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE evidence_artifacts DROP CONSTRAINT ck_evidence_artifacts_type"))
    op.execute(text("""
        ALTER TABLE evidence_artifacts ADD CONSTRAINT ck_evidence_artifacts_type
        CHECK (artifact_type IN ('git_file', 'git_diff', 'db_query', 'deploy',
                                'alert_payload', 'service_snapshot', 'operator_guidance'))
    """))
    op.execute(text("""
        CREATE FUNCTION reject_evidence_artifact_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evidence artifacts are immutable';
        END;
        $$ LANGUAGE plpgsql;
    """))
    op.execute(text("""
        CREATE TRIGGER trg_evidence_artifacts_immutable
        BEFORE UPDATE ON evidence_artifacts
        FOR EACH ROW EXECUTE FUNCTION reject_evidence_artifact_update();
    """))
    op.execute(text("""
        CREATE FUNCTION reject_integration_config_secret() RETURNS trigger AS $$
        BEGIN
            IF jsonb_path_exists(NEW.config, '$.**.password')
               OR jsonb_path_exists(NEW.config, '$.**.passwd')
               OR jsonb_path_exists(NEW.config, '$.**.secret')
               OR jsonb_path_exists(NEW.config, '$.**.token')
               OR jsonb_path_exists(NEW.config, '$.**.api_key')
               OR jsonb_path_exists(NEW.config, '$.**.access_key') THEN
                RAISE EXCEPTION 'integration config may not contain credentials';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    op.execute(text("""
        CREATE TRIGGER trg_application_integrations_secret_free_config
        BEFORE INSERT OR UPDATE OF config ON application_integrations
        FOR EACH ROW EXECUTE FUNCTION reject_integration_config_secret();
    """))


def downgrade() -> None:
    op.execute(text("DROP TRIGGER IF EXISTS trg_application_integrations_secret_free_config ON application_integrations"))
    op.execute(text("DROP FUNCTION IF EXISTS reject_integration_config_secret()"))
    op.execute(text("DROP TRIGGER IF EXISTS trg_evidence_artifacts_immutable ON evidence_artifacts"))
    op.execute(text("DROP FUNCTION IF EXISTS reject_evidence_artifact_update()"))
    op.execute(text("ALTER TABLE evidence_artifacts DROP CONSTRAINT ck_evidence_artifacts_type"))
    op.execute(text("""
        ALTER TABLE evidence_artifacts ADD CONSTRAINT ck_evidence_artifacts_type
        CHECK (artifact_type IN ('git_file', 'git_diff', 'db_query', 'deploy',
                                'alert_payload', 'service_snapshot'))
    """))
