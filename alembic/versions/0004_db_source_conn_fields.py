"""add structured connection fields to db_sources

Revision ID: 0004_db_source_conn_fields
Revises: 0003_add_memory_embedding
Create Date: 2026-08-23

Supports a complete "add data source" flow: an admin can either supply
structured PostgreSQL connection fields (host / port / database / username /
password) directly in the UI, or keep using a secret reference (conn_secret_ref
= env:// / vault:// / bare DSN). The two are mutually exclusive at the schema
layer (see CreateDbSourceIn).

* Adds nullable host / port / database / username / password columns.
* Drops the NOT NULL on conn_secret_ref so a source created purely from
  structured fields can store NULL there.

Downgrade restores the original shape (conn_secret_ref NOT NULL, columns
dropped). Existing rows that only used conn_secret_ref are untouched.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_db_source_conn_fields"
down_revision: Union[str, Sequence[str], None] = "0003_add_memory_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("db_sources", sa.Column("host", sa.Text(), nullable=True))
    op.add_column("db_sources", sa.Column("port", sa.Integer(), nullable=True))
    op.add_column("db_sources", sa.Column("database", sa.Text(), nullable=True))
    op.add_column("db_sources", sa.Column("username", sa.Text(), nullable=True))
    op.add_column("db_sources", sa.Column("password", sa.Text(), nullable=True))
    op.alter_column(
        "db_sources", "conn_secret_ref", existing_type=sa.Text(), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "db_sources", "conn_secret_ref", existing_type=sa.Text(), nullable=False
    )
    op.drop_column("db_sources", "password")
    op.drop_column("db_sources", "username")
    op.drop_column("db_sources", "database")
    op.drop_column("db_sources", "port")
    op.drop_column("db_sources", "host")
