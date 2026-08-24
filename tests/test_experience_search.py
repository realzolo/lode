"""Hermetic tests for pgvector semantic shared experience (M5).

These tests do not touch a database or the network (the sandbox DB is
unreachable). Embedding generation and pgvector retrieval are exercised through
injectable ``embed_fn`` / ``search_fn`` stubs, while the real ``embed`` client
is covered by monkeypatching ``urllib.request.urlopen``. Cosine math and the
experience lookup decision logic are tested directly.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

import pytest

from lode.db.vector import EMBEDDING_DIM
from lode.engine.embeddings import (
    EmbeddingConfig,
    build_query_text,
    cosine_distance,
    cosine_similarity,
    embed,
)
from lode.engine.experience_search import semantic_search
from lode.engine.tools import get_experience


class _ExperienceRow:
    """Minimal stand-in for an ORM Experience row."""

    def __init__(self, id: int, content: str, embedding=None) -> None:
        self.id = id
        self.content = content
        self.embedding = embedding


class _FakeResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeSession:
    """Returns a fixed exact-match experience (or None) for the exact path."""

    def __init__(self, exact_experience=None, rows=None) -> None:
        self._exact = exact_experience
        self._rows = rows if rows is not None else []

    async def execute(self, stmt):
        if self._exact is not None:
            return _FakeResult([self._exact])
        return _FakeResult(self._rows)


class _Alert:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


# --- cosine math ---------------------------------------------------------


def test_cosine_similarity_basics():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_cosine_similarity_degenerate():
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [3.0, 4.0, 5.0]) == 0.0


def test_cosine_distance():
    assert cosine_distance([1.0, 0.0], [1.0, 0.0]) == 0.0
    assert abs(cosine_distance([1.0, 0.0], [0.0, 1.0]) - 1.0) < 1e-9


# --- build_query_text ----------------------------------------------------


def test_build_query_text_includes_salient_fields():
    a = _Alert(
        title="Checkout 500s",
        level="critical",
        error_message="upstream timeout",
        fields={"service": "payments", "region": "us-east-1"},
    )
    text = build_query_text(a)
    assert "Incident: Checkout 500s" in text
    assert "Level: critical" in text
    assert "Error: upstream timeout" in text
    assert "service=payments" in text
    assert "region=us-east-1" in text


def test_build_query_text_handles_missing_alert():
    assert build_query_text(None) == ""


# --- embed() client ------------------------------------------------------


async def test_embed_disabled_without_config():
    assert await embed("anything", None) is None


async def test_embed_disabled_with_empty_key():
    cfg = EmbeddingConfig(base_url="https://x/embeddings", api_key_ref="", model="m")
    assert await embed("anything", cfg) is None


async def test_embed_disabled_with_unset_env_ref():
    cfg = EmbeddingConfig(
        base_url="https://x/embeddings", api_key_ref="env://LODE_TEST_UNSET", model="m"
    )
    assert await embed("anything", cfg) is None


async def test_embed_real_payload_and_extraction(monkeypatch):
    captured: dict = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode()

    def _fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["headers"] = req.headers
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    # resolve_api_key is strict: a key reference must be `env://NAME` or an
    # encrypted token, never a plaintext literal. Use the env form here.
    monkeypatch.setenv("LODE_TEST_EMBED_KEY", "sk-test-123")

    cfg = EmbeddingConfig(
        base_url="https://api.openai.com/v1/embeddings",
        api_key_ref="env://LODE_TEST_EMBED_KEY",
        model="text-embedding-3-small",
        dimensions=EMBEDDING_DIM,
    )
    vec = await embed("hello world", cfg)
    assert vec == [0.1, 0.2, 0.3]
    assert captured["url"] == "https://api.openai.com/v1/embeddings"
    body = json.loads(captured["data"])
    assert body["model"] == "text-embedding-3-small"
    assert body["input"] == "hello world"
    assert body["dimensions"] == EMBEDDING_DIM
    assert captured["headers"].get("Authorization") == "Bearer sk-test-123"


async def test_embed_real_appends_embeddings_path(monkeypatch):
    captured: dict = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"data": [{"embedding": [0.0]}]}).encode()

    def _fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setenv("LODE_TEST_EMBED_KEY2", "k")

    cfg = EmbeddingConfig(
        base_url="https://example.com/v1/", api_key_ref="env://LODE_TEST_EMBED_KEY2", model="m"
    )
    await embed("x", cfg)
    assert captured["url"] == "https://example.com/v1/embeddings"


async def test_embed_real_failure_degrades(monkeypatch):
    def _fake_urlopen(req, timeout=30):
        raise OSError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setenv("LODE_TEST_EMBED_KEY3", "k")

    cfg = EmbeddingConfig(base_url="https://x/embeddings", api_key_ref="env://LODE_TEST_EMBED_KEY3", model="m")
    assert await embed("x", cfg) is None


# --- get_experience decision logic -------------------------------------------


async def test_get_experience_exact_override():
    exp = _ExperienceRow(42, "recorded conclusion")
    sess = _FakeSession(exact_experience=exp)
    res = await get_experience(sess, 1, dedupe_key="abc123")
    assert res["matched"] is True
    assert res["experience_id"] == 42
    assert res["content"] == "recorded conclusion"
    assert res["match_type"] == "exact"
    assert res["similarity"] == 1.0


async def test_get_experience_semantic_match_within_threshold():
    exp = _ExperienceRow(7, "similar prior incident")
    async def fake_embed(text):
        return [0.5] * EMBEDDING_DIM

    async def fake_search(s, app_id, vec, top_k):
        return [(exp, 0.1)]

    sess = _FakeSession()
    res = await get_experience(
        sess,
        1,
        query_text="payment timeout",
        embed_fn=fake_embed,
        search_fn=fake_search,
        threshold=0.25,
    )
    assert res["matched"] is True
    assert res["experience_id"] == 7
    assert res["match_type"] == "semantic"
    assert res["similarity"] == round(1 - 0.1, 4)
    assert res["candidates"][0]["experience_id"] == 7


async def test_get_experience_semantic_rejected_above_threshold():
    exp = _ExperienceRow(7, "distant prior incident")
    async def fake_embed(text):
        return [0.5] * EMBEDDING_DIM

    async def fake_search(s, app_id, vec, top_k):
        return [(exp, 0.9)]

    sess = _FakeSession()
    res = await get_experience(
        sess,
        1,
        query_text="payment timeout",
        embed_fn=fake_embed,
        search_fn=fake_search,
        threshold=0.25,
    )
    assert res["matched"] is False
    assert res["match_type"] == "semantic"
    assert res["candidates"] == [
        {"experience_id": 7, "distance": 0.9, "similarity": 0.1}
    ]


async def test_get_experience_falls_back_when_no_embedding_wired():
    sess = _FakeSession()
    res = await get_experience(sess, 1, query_text="x")
    assert res["matched"] is False
    assert res["match_type"] == "none"


async def test_get_experience_exact_still_works_without_embedding():
    exp = _ExperienceRow(9, "exact via dedupe")
    sess = _FakeSession(exact_experience=exp)
    res = await get_experience(sess, 1, query_text="x", dedupe_key="k")
    assert res["matched"] is True
    assert res["experience_id"] == 9
    assert res["match_type"] == "exact"


async def test_get_experience_embed_failure_falls_back():
    async def fake_embed(text):
        return None

    async def fake_search(s, app_id, vec, top_k):
        return []

    sess = _FakeSession()
    res = await get_experience(
        sess, 1, query_text="x", embed_fn=fake_embed, search_fn=fake_search
    )
    assert res["matched"] is False
    assert res["match_type"] == "none"


# --- semantic_search (Python cosine ranking over DB rows) -----------------


async def test_semantic_search_ranks_by_cosine_distance():
    # Query vector points along [1, 0]; exp1 is identical, exp2 orthogonal,
    # exp3 opposite.
    query = [1.0, 0.0]
    exp1 = _ExperienceRow(1, "identical", [1.0, 0.0])
    exp2 = _ExperienceRow(2, "orthogonal", [0.0, 1.0])
    exp3 = _ExperienceRow(3, "opposite", [-1.0, 0.0])
    exp_null = _ExperienceRow(4, "no embedding", None)
    sess = _FakeSession(rows=[exp3, exp_null, exp1, exp2])

    ranked = await semantic_search(sess, 1, query, top_k=3)
    ids = [m.id for m, _ in ranked]
    # Null embedding excluded; rest sorted by ascending distance.
    assert ids == [1, 2, 3]
    # Distances: exp1=0, exp2=1, exp3=2.
    assert ranked[0][1] == 0.0
    assert abs(ranked[1][1] - 1.0) < 1e-9
    assert abs(ranked[2][1] - 2.0) < 1e-9


async def test_semantic_search_respects_top_k():
    query = [1.0, 0.0, 0.0]
    rows = [_ExperienceRow(i, f"m{i}", [float(i), 0.0, 0.0]) for i in range(1, 6)]
    sess = _FakeSession(rows=rows)
    ranked = await semantic_search(sess, 1, query, top_k=2)
    assert len(ranked) == 2
    # Closest to [1,0,0] are the ones with smallest first-component distance.
    assert [m.id for m, _ in ranked] == [1, 2]
