"""add sslmode and per-source sensitive_columns to db_sources

Revision ID: 0005_db_source_ssl_sensitive
Revises: 0004_db_source_conn_fields
Create Date: 2026-08-23

Follow-up hardening to the structured data-source flow:

* ``sslmode`` (nullable Text) — forces TLS on structured (host-based)
  connections so a cross-network link to a production replica cannot silently
  downgrade to cleartext. ``NULL`` keeps libpq's default (prefer).
* ``sensitive_columns`` (JSONB, default []) — operator-supplied extra column
  names masked in query results on top of the built-in heuristic hints.

Downgrade drops both columns, restoring the 0004 shape.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_db_source_ssl_sensitive"
down_revision: Union[str, Sequence[str], None] = "0004_db_source_conn_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("db_sources", sa.Column("sslmode", sa.Text(), nullable=True))
    op.add_column(
        "db_sources",
        sa.Column(
            "sensitive_columns",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("db_sources", "sensitive_columns")
    op.drop_column("db_sources", "sslmode")
