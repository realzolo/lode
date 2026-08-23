"""add application/partition/offset linkage to dead_letters

Revision ID: 0010_dead_letter_link
Revises: 0009_intake_jobs
Create Date: 2026-08-23

``dead_letters`` previously only captured the topic and raw payload. To make a
dead letter replayable and to attribute it to the originating application (when
known), we add ``application_id`` (FK, nullable — unassigned messages have no
app), plus ``partition``/``offset`` so a record can be traced back to its exact
Kafka position for audit. Forward-only.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_dead_letter_link"
down_revision: Union[str, Sequence[str], None] = "0009_intake_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dead_letters",
        sa.Column("application_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "dead_letters", sa.Column("partition", sa.Integer(), nullable=True)
    )
    op.add_column(
        "dead_letters", sa.Column("offset", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_dead_letters_application_id_applications",
        "dead_letters",
        "applications",
        ["application_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_dead_letters_application_id_applications",
        "dead_letters",
        type_="foreignkey",
        use_alter=True,
    )
    op.drop_column("dead_letters", "offset")
    op.drop_column("dead_letters", "partition")
    op.drop_column("dead_letters", "application_id")
