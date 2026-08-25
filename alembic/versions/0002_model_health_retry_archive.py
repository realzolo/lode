"""Add model health and investigation lifecycle controls.

Revision ID: 0002_model_health_retry_archive
Revises: 0001_initial
Create Date: 2026-08-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_model_health_retry_archive"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_model_configs",
        sa.Column("last_test_status", sa.Text(), server_default="untested", nullable=False),
    )
    op.add_column("ai_model_configs", sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_model_configs", sa.Column("last_test_latency_ms", sa.Integer(), nullable=True))
    op.add_column("ai_model_configs", sa.Column("last_test_error_code", sa.Text(), nullable=True))
    op.add_column("ai_model_configs", sa.Column("last_test_error_detail", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_ai_model_configs_last_test_status"),
        "ai_model_configs",
        "last_test_status IN ('untested', 'available', 'unavailable')",
    )

    op.add_column("investigations", sa.Column("retry_of_id", sa.BigInteger(), nullable=True))
    op.add_column("investigations", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("investigations", sa.Column("archived_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_investigations_retry_of_id_investigations",
        "investigations",
        "investigations",
        ["retry_of_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_investigations_archived_by_users",
        "investigations",
        "users",
        ["archived_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_investigations_retry_of", "investigations", ["retry_of_id"], unique=False)

    op.add_column("investigation_ai_invocations", sa.Column("error_detail", sa.Text(), nullable=True))
    op.add_column(
        "investigation_ai_invocations",
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("investigation_ai_invocations", "attempt_count")
    op.drop_column("investigation_ai_invocations", "error_detail")

    op.drop_index("ix_investigations_retry_of", table_name="investigations")
    op.drop_constraint("fk_investigations_archived_by_users", "investigations", type_="foreignkey")
    op.drop_constraint("fk_investigations_retry_of_id_investigations", "investigations", type_="foreignkey")
    op.drop_column("investigations", "archived_by")
    op.drop_column("investigations", "archived_at")
    op.drop_column("investigations", "retry_of_id")

    op.drop_constraint("ck_ai_model_configs_last_test_status", "ai_model_configs", type_="check")
    op.drop_column("ai_model_configs", "last_test_error_detail")
    op.drop_column("ai_model_configs", "last_test_error_code")
    op.drop_column("ai_model_configs", "last_test_latency_ms")
    op.drop_column("ai_model_configs", "last_tested_at")
    op.drop_column("ai_model_configs", "last_test_status")
