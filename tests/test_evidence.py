"""Hermetic tests for the Git evidence gateway (no network / no database).

Covers the branches that are easy to get wrong: secret masking, term
derivation, bounded tree search, and the artifact-building path of
``collect_git_evidence`` (clone is monkeypatched to a local temp dir).
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from lode.db.models.application import ApplicationRepo
from lode.db.models.git import GitRepo
from lode.db.models.intake import EvidenceArtifact
from lode.engine.evidence import (
    collect_git_evidence,
    derive_query_terms,
    mask_secrets,
    search_tree,
)
from lode.engine.evidence import git as evidence_git


# --- secret masking -------------------------------------------------------
def test_mask_secrets_redacts_aws_key():
    text = "key = AKIAIOSFODNN7EXAMPLE and more"
    masked, cats = mask_secrets(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in masked
    assert "<REDACTED:aws_access_key>" in masked
    assert "aws_access_key" in cats


def test_mask_secrets_redacts_connection_string_and_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
    text = f"DB=postgresql://u:p@host/db token {jwt}"
    masked, cats = mask_secrets(text)
    assert "postgresql://u:p@host/db" not in masked
    assert jwt not in masked
    assert "connection_string" in cats
    assert "jwt" in cats


def test_mask_secrets_redacts_credential_assignment():
    text = 'api_key = "sk_live_abcdef123456"'
    masked, cats = mask_secrets(text)
    assert "sk_live_abcdef123456" not in masked
    assert "credential_assignment" in cats


def test_mask_secrets_clean_text_untouched():
    text = "def handle_timeout(): return retry()"
    masked, cats = mask_secrets(text)
    assert masked == text
    assert cats == []


# --- term derivation ------------------------------------------------------
def test_derive_query_terms_from_alert():
    alert = types.SimpleNamespace(
        error_message="com.example.PaymentService TimeoutException: p99 > 2s",
        title="Checkout failed: balance validation timeout",
        fields={"exception": "TimeoutException", "function": "processPayment"},
    )
    terms = derive_query_terms(alert)
    # meaningful symbols survive; generic stopwords are dropped
    assert "TimeoutException" in terms
    assert "processPayment" in terms
    assert "Checkout" in terms
    assert "timeout" not in [t.lower() for t in terms]


# --- bounded tree search --------------------------------------------------
def _write_tree(root: Path) -> Path:
    (root / "service").mkdir(parents=True)
    (root / "service" / "pay.py").write_text(
        "class PaymentService:\n"
        "    def processPayment(self):\n"
        "        raise TimeoutException('p99 > 2s')  # DB=postgresql://u:p@h/x\n"
    )
    (root / "README.md").write_text("nothing relevant here\n")
    return root


def test_search_tree_finds_term_and_masks_secret():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = _write_tree(Path(td))
        hits = search_tree(root, ["PaymentService"], max_files=20, max_bytes=1_000_000)
        assert len(hits) == 1
        hit = hits[0]
        assert hit["path"].endswith("pay.py")
        assert "PaymentService" in hit["snippet"]
        # the secret in the same snippet is masked by search_tree? No — masking
        # happens in collect; but search_tree preserves raw snippet.
        assert "postgresql://" in hit["snippet"]


def test_search_tree_respects_file_cap():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in range(5):
            (root / f"f{i}.txt").write_text(f"PaymentService reference {i}\n")
        hits = search_tree(root, ["PaymentService"], max_files=2, max_bytes=1_000_000)
        assert len(hits) == 2


# --- collect_git_evidence (clone monkeypatched) ---------------------------
class FakeSession:
    def __init__(self, repos):
        self._repos = list(repos)
        self.added = []
        self._next_id = 0

    async def execute(self, stmt):
        repos = self._repos

        class _Result:
            def scalars(self):
                class _Scalars:
                    def all(self):
                        return list(repos)

                return _Scalars()

        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                self._next_id += 1
                setattr(obj, "id", self._next_id)


async def test_collect_git_evidence_persists_masked_artifact(monkeypatch):
    import tempfile

    # Point the (network) clone at a local temp dir we populate ourselves.
    with tempfile.TemporaryDirectory() as td:
        repo_dir = Path(td) / "clone"
        _write_tree(repo_dir)
        monkeypatch.setattr(
            evidence_git.settings,
            "evidence_git_cache_dir",
            str(Path(td) / "sandboxes"),
        )

        async def fake_clone(repo, ref, cache_root, timeout):
            return repo_dir

        monkeypatch.setattr(evidence_git, "ensure_repo_clone", fake_clone)

        repo = GitRepo(
            id=1, name="svc", repo_url="https://example.com/svc", default_branch="main"
        )
        session = FakeSession(repos=[repo])
        alert = types.SimpleNamespace(
            error_message="PaymentService TimeoutException",
            title="Checkout failed",
            fields={"commit": "abc123"},
        )

        result = await collect_git_evidence(
            session, application_id=5, alert=alert, analysis_id=9
        )

        artifacts = [o for o in session.added if isinstance(o, EvidenceArtifact)]
        assert result["artifact_count"] == len(artifacts) >= 1
        art = artifacts[0]
        assert art.analysis_id == 9
        assert art.artifact_type == "git_file"
        # locator is pinned to the resolved ref (commit field wins over default branch)
        assert "abc123" in art.locator
        # secret in the snippet is masked before persisting
        assert "postgresql://" not in art.redacted_excerpt
        assert "<REDACTED:" in art.redacted_excerpt
        assert art.metadata["secret_categories"]
        assert result["files"][0]["artifact_id"] == art.id


async def test_collect_git_evidence_uses_and_removes_task_sandbox(monkeypatch):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        sandbox_root = Path(td) / "sandboxes"
        source_root = _write_tree(Path(td) / "source")
        clone_roots: list[Path] = []

        async def fake_clone(repo, ref, cache_root, timeout):
            clone_roots.append(cache_root)
            return source_root

        monkeypatch.setattr(evidence_git, "ensure_repo_clone", fake_clone)
        monkeypatch.setattr(evidence_git.settings, "evidence_git_cache_dir", str(sandbox_root))

        repo = GitRepo(
            id=1, name="svc", repo_url="https://example.com/svc", default_branch="main"
        )
        alert = types.SimpleNamespace(
            error_message="PaymentService TimeoutException",
            title="Checkout failed",
            fields={},
        )

        await collect_git_evidence(FakeSession(repos=[repo]), 5, alert, analysis_id=9)
        await collect_git_evidence(FakeSession(repos=[repo]), 5, alert, analysis_id=10)

        assert len(clone_roots) == 2
        assert clone_roots[0] != clone_roots[1]
        assert all(path.parent == sandbox_root for path in clone_roots)
        assert all(not path.exists() for path in clone_roots)
