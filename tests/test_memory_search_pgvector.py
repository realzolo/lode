"""Tests for the optional pgvector semantic-search backend.

These validate the pgvector code path *without* a live pgvector database: the
generated SQL is compiled against the PostgreSQL dialect (asserting it uses the
``<=>`` cosine operator and casts the stored ``real[]`` column to ``vector``),
and the automatic fallback to the Python backend is verified by forcing the
pgvector path to raise.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from lode.engine import memory_search


def test_pgvector_query_uses_cosine_operator():
    stmt = memory_search._build_pgvector_stmt(7, [0.1, 0.2, 0.3], top_k=3)
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "<=>" in sql, "pgvector backend must use the <=> cosine operator"
    assert "vector" in sql.lower(), "real[] column must be cast to vector"
    assert "limit" in sql.lower(), "top_k must be applied as a LIMIT"
    assert "application_id" in sql


async def test_pgvector_falls_back_to_python_on_error(monkeypatch):
    async def _raise(*args, **kwargs):
        raise RuntimeError("pgvector extension unavailable")

    async def _python(*a, **k):
        return [("mem", 0.12)]

    monkeypatch.setattr(memory_search, "_semantic_search_pgvector", _raise)
    monkeypatch.setattr(memory_search, "_semantic_search_python", _python)
    result = await memory_search.semantic_search(object(), 1, [0.1], backend="pgvector")
    assert result == [("mem", 0.12)]


async def test_python_backend_is_default_and_skips_pgvector(monkeypatch):
    called = {}

    async def _pg(*a, **k):
        called["pg"] = True
        return []

    async def _python(*a, **k):
        return [("m", 0.0)]

    monkeypatch.setattr(memory_search, "_semantic_search_pgvector", _pg)
    monkeypatch.setattr(memory_search, "_semantic_search_python", _python)
    result = await memory_search.semantic_search(object(), 1, [0.1])
    assert result == [("m", 0.0)]
    assert "pg" not in called
