"""add semantic embedding to memories

Revision ID: 0003_add_memory_embedding
Revises: 0002_add_password_hash
Create Date: 2026-08-23

Enables semantic shared memory (M5). Adds a nullable ``embedding real[]``
column to ``memories``.

The embedding is stored as a native PostgreSQL ``real[]`` array so the feature
works without the ``pgvector`` extension. Cosine-similarity retrieval is done
in Python over the per-application candidate set (see
``lode.engine.memory_search``). When pgvector is provisioned in the target
database, this column can be migrated to ``VECTOR(1536)`` with an HNSW cosine
index for large-scale ANN search without changing application logic.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from lode.db.vector import EMBEDDING_DIM

revision: str = "0003_add_memory_embedding"
down_revision: Union[str, None] = "0002_add_password_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Native array column: no extension required, works on any PostgreSQL.
    op.add_column(
        "memories",
        sa.Column("embedding", sa.ARRAY(sa.Float), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memories", "embedding")
