"""enforce secure PostgreSQL data-source bindings

Revision ID: 0009_secure_db_sources
Revises: 0008_evidence_immutability
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0009_secure_db_sources"
down_revision: str | Sequence[str] | None = "0008_evidence_immutability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(text("""
        ALTER TABLE db_sources ADD CONSTRAINT ck_db_sources_secure_connection
        CHECK (
            (
                conn_secret_ref IS NOT NULL
                AND conn_secret_ref ~ '^env://[A-Za-z_][A-Za-z0-9_]*$'
                AND host IS NULL AND port IS NULL AND database IS NULL
                AND username IS NULL AND password IS NULL AND sslmode IS NULL
            )
            OR
            (
                conn_secret_ref IS NULL
                AND host IS NOT NULL AND database IS NOT NULL
                AND sslmode = 'verify-full'
            )
        )
    """))


def downgrade() -> None:
    op.execute(text("ALTER TABLE db_sources DROP CONSTRAINT ck_db_sources_secure_connection"))
