"""Embedding dimensionality + storage notes for semantic memory.

Semantic memory stores each memory's embedding as a native PostgreSQL
``real[]`` array. This keeps the feature dependency-free: it works against any
PostgreSQL (the local dev DB, the ``postgres:16`` compose image, and managed
hosts) without requiring the ``pgvector`` extension to be installed.

Cosine similarity / ranking is computed in Python over the small per-application
candidate set (see ``lode.engine.memory_search``), which is more than fast
enough for shared-memory volumes and stays fully hermetic-testable.

Upgrade path to pgvector (for large-scale ANN indexing): swap the column type
to ``pgvector.sqlalchemy.Vector(EMBEDDING_DIM)`` here and in the migration, add
``CREATE EXTENSION vector`` + an HNSW cosine index, and let ``memory_search``
use the ``<=>`` operator. No application logic changes.
"""

from __future__ import annotations

# Fixed embedding dimensionality. The configured embedding model's output size
# must match this constant. text-embedding-3-small (the default) emits
# 1536-dim vectors.
EMBEDDING_DIM: int = 1536
