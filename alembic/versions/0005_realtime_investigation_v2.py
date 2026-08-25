"""Add real-time plan deltas and the citable investigation reasoning graph."""

from alembic import op
from sqlalchemy import text


revision = "0005_realtime_investigation_v2"
down_revision = "0004_dynamic_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = [
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS conclusion_version integer NOT NULL DEFAULT 0;",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS superseded_by_investigation_id bigint REFERENCES investigations(id) ON DELETE SET NULL;",
        "ALTER TABLE investigation_plan_revisions ADD COLUMN IF NOT EXISTS wave integer NOT NULL DEFAULT 0;",
        "ALTER TABLE investigation_plan_revisions ADD COLUMN IF NOT EXISTS change_set jsonb NOT NULL DEFAULT '{}'::jsonb;",
        "ALTER TABLE investigation_plan_revisions DROP CONSTRAINT IF EXISTS plan_decision;",
        "ALTER TABLE investigation_plan_revisions ADD CONSTRAINT plan_decision CHECK (decision IN ('initial', 'continue', 'conclude', 'add', 'cancel', 'reorder', 'converge', 'request_evidence'));",
        "ALTER TABLE investigation_findings DROP CONSTRAINT IF EXISTS finding_kind;",
        "ALTER TABLE investigation_findings ADD CONSTRAINT finding_kind CHECK (kind IN ('fact', 'hypothesis', 'counter_evidence', 'impact', 'evidence_gap', 'conclusion'));",
        """
        CREATE TABLE investigation_finding_edges (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            from_finding_id bigint NOT NULL REFERENCES investigation_findings(id) ON DELETE CASCADE,
            to_finding_id bigint NOT NULL REFERENCES investigation_findings(id) ON DELETE CASCADE,
            relation text NOT NULL CHECK (relation IN ('supports', 'contradicts', 'caused_by', 'needs_test')),
            evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT finding_edge_not_self CHECK (from_finding_id <> to_finding_id)
        );
        """,
        "CREATE INDEX ix_investigation_finding_edges_run ON investigation_finding_edges(investigation_id, from_finding_id, to_finding_id);",
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError("Investigation v2 reasoning records are immutable audit data; restore a database backup instead.")
