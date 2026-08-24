"""analysis task identity and intake workflow boundary

Revision ID: 0003_analysis_task_identity
Revises: 0002_app_ingestion_lifecycle
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0003_analysis_task_identity"
down_revision: str | Sequence[str] | None = "0002_app_ingestion_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        "ALTER TABLE analyses ADD COLUMN public_id text;",
        "UPDATE analyses SET public_id = md5(random()::text || clock_timestamp()::text || id::text) WHERE public_id IS NULL;",
        "ALTER TABLE analyses ALTER COLUMN public_id SET NOT NULL;",
        "ALTER TABLE analyses ADD CONSTRAINT uq_analyses_public_id UNIQUE (public_id);",
        """
        DELETE FROM analysis_steps older
        USING analysis_steps newer
        WHERE older.analysis_id = newer.analysis_id
          AND older.node_type = newer.node_type
          AND older.id > newer.id;
        """,
        """
        INSERT INTO analysis_steps (
            analysis_id, node_type, status, input, output, order_index,
            started_at, finished_at
        )
        SELECT
            a.id,
            'receive',
            'completed',
            jsonb_build_object('topic', COALESCE(alert.topic, '')),
            jsonb_build_object(
                'summary', 'Alert received',
                'detail', 'Routed via topic ' || COALESCE(alert.topic, 'n/a')
            ),
            0,
            COALESCE(alert.received_at, a.created_at),
            COALESCE(alert.received_at, a.created_at)
        FROM analyses a
        LEFT JOIN alerts alert ON alert.id = a.alert_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM analysis_steps step
            WHERE step.analysis_id = a.id
              AND step.node_type = 'receive'
        );
        """,
        "ALTER TABLE analysis_steps ADD CONSTRAINT uq_analysis_steps_analysis_node UNIQUE (analysis_id, node_type);",
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    statements = [
        "ALTER TABLE analysis_steps DROP CONSTRAINT IF EXISTS uq_analysis_steps_analysis_node;",
        "DELETE FROM analysis_steps WHERE node_type = 'receive';",
        "ALTER TABLE analyses DROP CONSTRAINT IF EXISTS uq_analyses_public_id;",
        "ALTER TABLE analyses DROP COLUMN IF EXISTS public_id;",
    ]
    for statement in statements:
        op.execute(text(statement))
