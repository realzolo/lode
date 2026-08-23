"""Semantic retrieval for shared memory.

This module owns the *database* half of semantic memory: given a query vector,
fetch the valid memories for an application that carry an embedding and rank
them by cosine distance in Python. We rank in Python (rather than via the
pgvector ``<=>`` operator) so the feature works against any PostgreSQL without
the ``pgvector`` extension and stays fully hermetic-testable. The candidate set
is per-application and small, so the in-process ranking is negligible.

The embedding of the query text and the selection/threshold logic live in
``lode.engine.tools.get_memory`` so retrieval here stays a pure, injectable
dependency (hermetic tests swap it for an in-memory stub).
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import select

from lode.db.models.memory import Memory
from lode.engine.embeddings import cosine_distance


async def semantic_search(
    session,
    application_id: int,
    query_vec: list[float],
    *,
    top_k: int = 5,
) -> list[tuple[Memory, float]]:
    """Return up to ``top_k`` valid memories with their cosine distance.

    Distance is in [0, 2] (0 = identical direction). Only memories that
    actually carry an embedding participate; older/legacy rows are ignored
    here and remain reachable via the exact trigger_signature match in
    ``get_memory``.
    """
    stmt = (
        select(Memory)
        .where(Memory.application_id == application_id)
        .where(Memory.is_valid.is_(True))
        .where(Memory.embedding.isnot(None))
    )
    rows = (await session.execute(stmt)).scalars().all()

    scored: list[tuple[Memory, float]] = []
    for mem in rows:
        emb = mem.embedding
        if not emb:
            continue
        scored.append((mem, cosine_distance(query_vec, list(emb))))

    scored.sort(key=lambda pair: pair[1])
    return scored[:top_k]
