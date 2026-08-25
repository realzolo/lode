"""Add bounded live-progress facts for the single-page investigation workbench."""

from alembic import op
from sqlalchemy import text


revision = "0006_investigation_live_progress"
down_revision = "0005_realtime_investigation_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(text("ALTER TABLE investigation_execution_events DROP CONSTRAINT IF EXISTS phase;"))
    op.execute(text("ALTER TABLE investigation_execution_events DROP CONSTRAINT IF EXISTS investigation_execution_events_phase_check;"))
    op.execute(text("ALTER TABLE investigation_execution_events ADD CONSTRAINT phase CHECK (phase IN ('started', 'progress', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured', 'canceled'));"))


def downgrade() -> None:
    raise RuntimeError("Live investigation progress is immutable audit data; restore a database backup instead of downgrading.")
