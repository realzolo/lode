"""Semantic retrieval for shared experience.

This module owns the *database* half of semantic experience: given a query vector,
return the valid experiences for an application ranked by cosine distance.

Two backends are supported, selected by ``settings.embedding_backend``:

* ``"python"`` (default): fetch the candidate experiences and rank them in Python
  with ``cosine_distance`` (see ``lode.engine.embeddings``). Works against
  *any* PostgreSQL — no ``pgvector`` extension required — and is fully
  hermetic-testable. The per-application candidate set is small, so the
  in-process ranking is negligible.
* ``"pgvector"``: offload distance computation (and, with an HNSW index, ANN
  search) to the database using the ``<=>`` operator. The stored ``real[]``
  column is cast to ``vector`` at query time, so **no column-type migration is
  needed** and the portable ``real[]`` storage is preserved. If the ``pgvector``
  extension is missing on the host, or the query fails for any reason, the
  backend transparently falls back to the Python ranking so the feature never
  hard-breaks.

The embedding of the query text and the selection/threshold logic live in
``lode.engine.tools.get_experience`` so retrieval here stays a pure, injectable
dependency (hermetic tests swap it for an in-memory stub).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select

from lode.config import settings
from lode.db.models.experience import Experience
from lode.engine.embeddings import cosine_distance

_PGVECTOR_BACKENDS = frozenset({"pgvector"})


def _use_pgvector(backend: str | None) -> bool:
    return (backend or settings.embedding_backend) in _PGVECTOR_BACKENDS


def _not_expired_clause():
    """Clause excluding TTL-expired experiences (T8).

    NULL ``expires_at`` means permanent; otherwise the row must still be in the
    future. Computed once per call so all candidates in one search share a clock.
    """
    now_utc = datetime.now(UTC)
    return or_(Experience.expires_at.is_(None), Experience.expires_at > now_utc)


async def semantic_search(
    session,
    application_id: int,
    query_vec: list[float],
    *,
    top_k: int = 5,
    backend: str | None = None,
) -> list[tuple[Experience, float]]:
    """Return up to ``top_k`` valid experiences with their cosine distance.

    Distance is in [0, 2] (0 = identical direction). Only experiences that
    actually carry an embedding participate; older/legacy rows are ignored
    here and remain reachable via the exact trigger_signature match in
    ``get_experience``.

    The backend is chosen by ``backend`` (test override) or
    ``settings.embedding_backend``. The pgvector backend falls back to the
    Python ranking if the ``vector`` extension is unavailable or the query
    raises.
    """
    if _use_pgvector(backend):
        try:
            return await _semantic_search_pgvector(
                session, application_id, query_vec, top_k=top_k
            )
        except Exception:
            # Extension missing / unsupported cast / any DB error: degrade
            # gracefully to the in-process ranking rather than failing the
            # whole experience lookup.
            pass
    return await _semantic_search_python(
        session, application_id, query_vec, top_k=top_k
    )


def _build_pgvector_stmt(
    application_id: int,
    query_vec: list[float],
    *,
    top_k: int = 5,
):
    """Build the pgvector ``<=>`` ranking statement.

    The ``real[]`` column is cast to ``vector`` so the portable storage type is
    preserved while still using pgvector's native cosine operator. Extracted as
    a standalone function so the generated SQL can be unit-tested without a live
    pgvector database.
    """
    from pgvector.sqlalchemy import Vector

    return (
        select(
            Experience,
            Experience.embedding.cast(Vector).cosine_distance(query_vec).label("distance"),
        )
        .where(Experience.application_id == application_id)
        .where(Experience.is_valid.is_(True))
        .where(Experience.embedding.isnot(None))
        .where(_not_expired_clause())
        .order_by("distance")
        .limit(top_k)
    )


async def _semantic_search_pgvector(
    session,
    application_id: int,
    query_vec: list[float],
    *,
    top_k: int = 5,
) -> list[tuple[Experience, float]]:
    stmt = _build_pgvector_stmt(application_id, query_vec, top_k=top_k)
    rows = (await session.execute(stmt)).all()
    return [(mem, float(dist)) for mem, dist in rows]


async def _semantic_search_python(
    session,
    application_id: int,
    query_vec: list[float],
    *,
    top_k: int = 5,
) -> list[tuple[Experience, float]]:
    stmt = (
        select(Experience)
        .where(Experience.application_id == application_id)
        .where(Experience.is_valid.is_(True))
        .where(Experience.embedding.isnot(None))
        .where(_not_expired_clause())
    )
    rows = (await session.execute(stmt)).scalars().all()

    scored: list[tuple[Experience, float]] = []
    for mem in rows:
        emb = mem.embedding
        if not emb:
            continue
        scored.append((mem, cosine_distance(query_vec, list(emb))))

    scored.sort(key=lambda pair: pair[1])
    return scored[:top_k]
