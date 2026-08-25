"""Embedding dimensionality + pgvector integration notes for semantic experience.

Semantic experience stores each experience's embedding as a native PostgreSQL ``real[]``
array. This keeps the feature dependency-free: it works against any PostgreSQL
(the local dev DB, the ``postgres:16`` compose image, and managed hosts)
without requiring the ``pgvector`` extension to be installed.

Cosine ranking is computed in one of two backends, selected by
``settings.embedding_backend`` (see ``lode.engine.experience_search``):

* ``"python"`` (default): rank in Python over the small per-application
  candidate set. Fully hermetic-testable, no extension needed.
* ``"pgvector"``: offload to the database via the ``<=>`` operator by casting
  the stored ``real[]`` column to ``vector`` at query time. **No column-type
  migration is required** — the portable ``real[]`` storage is preserved, and
  if the ``vector`` extension is missing the search automatically falls back to
  the Python backend.

Optional HNSW acceleration (pgvector hosts only)
------------------------------------------------
When the ``vector`` extension is available and the table is large, add an HNSW
cosine index on the cast expression to turn the ``<=>`` scan into an ANN
lookup. This is a *manual, opt-in* step (kept out of the auto-migrate chain so
it never runs on extension-less hosts)::

    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE INDEX IF NOT EXISTS ix_experiences_embedding_hnsw
        ON experiences USING hnsw ((embedding::vector) vector_cosine_ops);

The cast ``embedding::vector`` relies on pgvector's array→vector parsing; if a
given pgvector build rejects it, wrap as ``(embedding::text::vector)``.

This module is retained only for general embedding utilities; the retired
experience store is not part of the V2 investigation schema.
"""

from __future__ import annotations

# Fixed embedding dimensionality. The configured embedding model's output size
# must match this constant. text-embedding-3-small (the default) emits
# 1536-dim vectors.
EMBEDDING_DIM: int = 1536
