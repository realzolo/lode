"""lock the alert envelope to the alert.v1 Kafka format

Revision ID: 0011_alert_v1_format
Revises: 0010_dead_letter_link
Create Date: 2026-08-24

The Kafka alert envelope is now fixed to the ``alert.v1`` shape emitted by
``lark-alert.ts`` (``KafkaAlertMessage``). That envelope carries
``alert_id``, ``occurred_at``, ``dedupe_key``, ``dedupe_ttl_seconds`` and a
structured ``error_log``, and it no longer carries ``env``. We therefore:

* drop the now-obsolete ``env`` column from ``alerts``,
* add ``alert_id`` (text), ``occurred_at`` (timestamptz),
  ``dedupe_ttl_seconds`` (int) and ``error_log`` (jsonb).

No backward-compatibility shim: old ``1.1``-shaped messages are rejected at the
consumer and routed to the DLQ, so the column layout is allowed to diverge
cleanly from the legacy schema. Forward-only.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_alert_v1_format"
down_revision: Union[str, Sequence[str], None] = "0010_dead_letter_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("alerts", "env")
    op.add_column("alerts", sa.Column("alert_id", sa.Text(), nullable=True))
    op.add_column(
        "alerts",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "alerts", sa.Column("dedupe_ttl_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "alerts", sa.Column("error_log", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("alerts", "error_log")
    op.drop_column("alerts", "dedupe_ttl_seconds")
    op.drop_column("alerts", "occurred_at")
    op.drop_column("alerts", "alert_id")
    # Re-add env as nullable on the way back so existing rows are not rejected.
    op.add_column("alerts", sa.Column("env", sa.Text(), nullable=True))
