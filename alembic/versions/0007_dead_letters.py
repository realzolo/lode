"""persist DLQ / unmapped-topic messages as dead_letters

Revision ID: 0007_dead_letters
Revises: 0006_git_repo_type
Create Date: 2026-08-23

The consumer still forwards bad messages to the Kafka DLQ / unassigned topics,
but those topics are not actively consumed, so failures were previously
invisible. This table makes them auditable and replayable via the API.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_dead_letters"
down_revision: Union[str, Sequence[str], None] = "0006_git_repo_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("replayed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dead_letters_kind", "dead_letters", ["kind"])
    op.create_index("ix_dead_letters_created_at", "dead_letters", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_dead_letters_created_at", table_name="dead_letters")
    op.drop_index("ix_dead_letters_kind", table_name="dead_letters")
    op.drop_table("dead_letters")
