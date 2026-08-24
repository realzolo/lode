"""make dead-letter writes idempotent per source record

Revision ID: 0012_dlq_idempotent
Revises: 0011_alert_v1_format
Create Date: 2026-08-24

A redelivered source record that routes to the DLQ/unassigned topic is now
produced with a key derived from its (topic, partition, offset) and persisted
under a unique constraint on those coordinates. This stops a crash between the
DLQ produce and the offset commit from creating duplicate dead letters — the
DLQ table becomes the authoritative, de-duplicated audit log instead of a
grow-only sink.

The constraint is partial (only Kafka-sourced rows with non-null partition and
offset participate), so manually-created / non-Kafka dead letters are unaffected.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0012_dlq_idempotent"
down_revision: Union[str, Sequence[str], None] = "0011_alert_v1_format"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_dead_letters_source"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "dead_letters",
        ["topic", "partition", "offset", "kind"],
        unique=True,
        postgresql_where=text('partition IS NOT NULL AND "offset" IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="dead_letters")
