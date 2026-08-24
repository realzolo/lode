"""add persisted AI output-language setting

Revision ID: 0005_platform_ai_output_language
Revises: 0004_alert_error_summary
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0005_platform_ai_output_language"
down_revision: str | Sequence[str] | None = "0004_alert_error_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE TABLE platform_settings (
                key text PRIMARY KEY,
                value text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT ck_platform_settings_ai_output_language
                    CHECK (key <> 'ai_output_language' OR value IN ('en', 'zh'))
            );
            """
        )
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS platform_settings;"))
