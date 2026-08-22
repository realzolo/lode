"""add password_hash to users

Revision ID: 0002_add_password_hash
Revises: 0001_initial
Create Date: 2026-08-21

Adds a nullable ``password_hash`` column to ``users`` so the platform can
authenticate with PBKDF2-HMAC-SHA256 (see ``lode.security``). The
column is nullable because invited users have no password until they activate
their account.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_add_password_hash"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")
