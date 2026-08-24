"""backfill controlled alert error summaries

Revision ID: 0004_alert_error_summary
Revises: 0003_analysis_task_identity
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0004_alert_error_summary"
down_revision: str | Sequence[str] | None = "0003_analysis_task_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("""
        UPDATE alerts
        SET error_message = left(
            coalesce(
                nullif(btrim(fields ->> 'error'), ''),
                nullif(btrim(fields ->> 'reason'), ''),
                nullif(btrim(fields ->> 'message'), ''),
                nullif(btrim(fields ->> 'detail'), '')
            ),
            500
        )
        WHERE btrim(error_message) = ''
          AND coalesce(
                nullif(btrim(fields ->> 'error'), ''),
                nullif(btrim(fields ->> 'reason'), ''),
                nullif(btrim(fields ->> 'message'), ''),
                nullif(btrim(fields ->> 'detail'), '')
          ) IS NOT NULL;
    """))


def downgrade() -> None:
    # Backfilled summaries intentionally remain: clearing them would discard
    # valid alert evidence and cannot safely distinguish prior manual values.
    pass
