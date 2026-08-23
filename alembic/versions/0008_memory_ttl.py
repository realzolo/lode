"""add expires_at TTL column to memories

Revision ID: 0008_memory_ttl
Revises: 0007_dead_letters
Create Date: 2026-08-23

Shared conclusions in the ``memories`` table are given a finite lease so a
long-resolved incident cannot keep shadowing fresh analyses forever (T8).
``expires_at`` is NULL for "never expires"; the runner stamps it from
``settings.memory_ttl_days`` on write, retrieval filters it out, and the
startup reaper retires stale rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_memory_ttl"
down_revision: str | Sequence[str] | None = "0007_dead_letters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memories_expires_at", "memories", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_memories_expires_at", table_name="memories")
    op.drop_column("memories", "expires_at")
