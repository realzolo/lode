"""Enforce complete Workspace ingestion activation state.

Revision ID: 0004_workspace_ingestion_state
Revises: 0003_schema_catalog_secret_scope
Create Date: 2026-08-28 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_workspace_ingestion_state"
down_revision: str | None = "0003_schema_catalog_secret_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATE_SHAPE = (
    "(ingestion_state = 'draft' AND ingestion_start_position IS NULL "
    "AND ingestion_activation_kind IS NULL AND ingestion_started_at IS NULL "
    "AND ingestion_paused_at IS NULL) OR "
    "(ingestion_state = 'active' AND ingestion_version > 0 "
    "AND ingestion_start_position IS NOT NULL AND ingestion_activation_kind IS NOT NULL "
    "AND ingestion_started_at IS NOT NULL AND ingestion_paused_at IS NULL) OR "
    "(ingestion_state = 'paused' AND ingestion_version > 0 "
    "AND ingestion_start_position IS NOT NULL AND ingestion_activation_kind IS NOT NULL "
    "AND ingestion_started_at IS NOT NULL AND ingestion_paused_at IS NOT NULL)"
)


def upgrade() -> None:
    # Older tests could bypass the control-plane transition and leave incomplete
    # active rows. Fail closed without deleting their audit history.
    op.execute(
        sa.text(
            "UPDATE workspaces SET ingestion_state = 'draft', "
            "ingestion_start_position = NULL, ingestion_activation_kind = NULL, "
            "ingestion_started_at = NULL, ingestion_paused_at = NULL "
            "WHERE NOT (" + _STATE_SHAPE + ")"
        )
    )
    op.create_check_constraint(
        op.f("ck_workspaces_ingestion_state_shape"),
        "workspaces",
        _STATE_SHAPE,
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_workspaces_ingestion_state_shape"),
        "workspaces",
        type_="check",
    )
