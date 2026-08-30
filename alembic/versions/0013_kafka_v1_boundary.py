"""Keep Kafka v1 context absence explicit in the incident model.

Revision ID: 0013_kafka_v1_boundary
Revises: 0012_incident_platform_rebuild
Create Date: 2026-08-30 22:00:00.000000

The immutable incident.alert.v1 contract contains neither component nor
environment. These internal projections therefore remain nullable for Kafka
occurrences instead of storing invented context.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_kafka_v1_boundary"
down_revision: str | None = "0012_incident_platform_rebuild"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("incidents", "component", existing_type=sa.Text(), nullable=True)
    op.alter_column("incidents", "environment", existing_type=sa.Text(), nullable=True)
    op.alter_column("incident_occurrences", "component", existing_type=sa.Text(), nullable=True)
    op.alter_column("incident_occurrences", "environment", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    raise RuntimeError("The Kafka v1 incident boundary has no compatibility downgrade path.")
