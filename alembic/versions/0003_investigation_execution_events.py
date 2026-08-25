"""Add immutable operation-level audit events to canonical investigations."""

from alembic import op
from sqlalchemy import text


# ``alembic_version.version_num`` originates in V1 as varchar(32), so every
# revision identifier must remain within that immutable storage limit.
revision = "0003_execution_events"
down_revision = "0002_canonical_investigations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = [
        """
        CREATE TABLE investigation_execution_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            stage_id bigint NOT NULL REFERENCES investigation_stages(id) ON DELETE CASCADE,
            collection_id bigint REFERENCES evidence_collections(id) ON DELETE CASCADE,
            operation_id text NOT NULL,
            sequence integer NOT NULL,
            event_type text NOT NULL,
            phase text NOT NULL CHECK (phase IN ('started', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured')),
            detail jsonb NOT NULL DEFAULT '{}'::jsonb,
            artifact_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(investigation_id, sequence)
        );
        """,
        "CREATE INDEX ix_investigation_execution_events_operation ON investigation_execution_events(investigation_id, stage_id, operation_id, sequence);",
        """
        CREATE FUNCTION prevent_investigation_execution_event_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'investigation execution events are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """,
        "CREATE TRIGGER trg_investigation_execution_events_immutable BEFORE UPDATE OR DELETE ON investigation_execution_events FOR EACH ROW EXECUTE FUNCTION prevent_investigation_execution_event_mutation();",
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError("Execution events are immutable audit data; restore a database backup instead of downgrading.")
