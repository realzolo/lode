"""Persist the investigation worker lifecycle phase.

Revision ID: 0007_investigation_job_phase
Revises: 0006_sql_scope_dialect
Create Date: 2026-08-30 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_investigation_job_phase"
down_revision: str | None = "0006_sql_scope_dialect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_investigations_status"),
        "investigations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_investigations_status"),
        "investigations",
        "status IN ('queued', 'running', 'reporting', 'completed', 'failed')",
    )
    op.add_column(
        "investigation_jobs",
        sa.Column(
            "phase",
            sa.Text(),
            server_default="investigation",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_investigation_jobs_phase"),
        "investigation_jobs",
        "phase IN ('investigation', 'reporting')",
    )
    op.drop_index("ix_investigation_jobs_claim", table_name="investigation_jobs")
    op.create_index(
        "ix_investigation_jobs_claim",
        "investigation_jobs",
        ["status", "phase", "available_at", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM investigations WHERE status = 'reporting') THEN "
            "RAISE EXCEPTION 'cannot downgrade while investigations are reporting'; "
            "END IF; END $$"
        )
    )
    op.drop_index("ix_investigation_jobs_claim", table_name="investigation_jobs")
    op.create_index(
        "ix_investigation_jobs_claim",
        "investigation_jobs",
        ["status", "available_at", "lease_expires_at"],
        unique=False,
    )
    op.drop_constraint(
        op.f("ck_investigation_jobs_phase"),
        "investigation_jobs",
        type_="check",
    )
    op.drop_column("investigation_jobs", "phase")
    op.drop_constraint(
        op.f("ck_investigations_status"),
        "investigations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_investigations_status"),
        "investigations",
        "status IN ('queued', 'running', 'completed', 'failed')",
    )
