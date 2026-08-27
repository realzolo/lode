from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lode.config import settings
from lode.infrastructure.git_source import (
    GitRemoteRevisionResolver,
    GitSourceReader,
)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(root: Path) -> str:
    root.mkdir()
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.email", "source-test@lode.local")
    _git(root, "config", "user.name", "Source Test")
    (root / "service.py").write_text(
        "def checkout():\n    timeout = 30\n    raise CheckoutTimeout(timeout)\n",
        encoding="utf-8",
    )
    _git(root, "add", "service.py")
    _git(root, "commit", "--quiet", "-m", "source fixture")
    return _git(root, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_git_source_is_resolved_and_read_at_the_exact_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    revision = _repository(repository)
    cache = tmp_path / "cache"
    monkeypatch.setattr(settings, "evidence_git_cache_dir", str(cache))
    resolver = GitRemoteRevisionResolver(timeout_seconds=5)

    assert (
        await resolver.resolve_branch(repo_url=str(repository), branch="main", credential=None)
        == revision
    )
    assert (
        await resolver.resolve_revision(
            repo_url=str(repository), revision=revision, credential=None
        )
        == revision
    )

    hits = await GitSourceReader(timeout_seconds=5).collect(
        repo_url=str(repository),
        revision=revision,
        credential=None,
        stack='File "service.py", line 3, in checkout',
        query_terms=("CheckoutTimeout",),
    )

    assert hits
    assert hits[0].path == "service.py"
    assert hits[0].start_line == 1
    assert "raise CheckoutTimeout" in hits[0].content
    assert not cache.exists() or not tuple(cache.iterdir())


@pytest.mark.asyncio
async def test_git_source_does_not_fallback_from_an_unknown_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    monkeypatch.setattr(settings, "evidence_git_cache_dir", str(tmp_path / "cache"))

    resolved = await GitRemoteRevisionResolver(timeout_seconds=5).resolve_revision(
        repo_url=str(repository), revision="a" * 40, credential=None
    )

    assert resolved is None


@pytest.mark.asyncio
async def test_git_source_rejects_unapproved_remote_schemes() -> None:
    with pytest.raises(ValueError, match="scheme"):
        await GitRemoteRevisionResolver().resolve_branch(
            repo_url="http://git.example/repository.git",
            branch="main",
            credential=None,
        )
