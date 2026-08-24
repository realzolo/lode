"""add integration updated_at trigger

Revision ID: 0007_integration_updated_at_trigger
Revises: 0006_readonly_integrations
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0007_integration_updated"
down_revision: str | Sequence[str] | None = "0006_readonly_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("DROP TRIGGER IF EXISTS trg_application_integrations_updated_at ON application_integrations"))
    op.execute(text("""
        CREATE TRIGGER trg_application_integrations_updated_at
        BEFORE UPDATE ON application_integrations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """))


def downgrade() -> None:
    op.execute(text("DROP TRIGGER IF EXISTS trg_application_integrations_updated_at ON application_integrations"))
