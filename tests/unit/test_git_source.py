from __future__ import annotations

import ipaddress
import subprocess
from pathlib import Path

import pytest

from lode.config import settings
from lode.infrastructure.git_source import (
    GitRemoteRevisionResolver,
    GitSourceReader,
    _git_egress_policy,
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


@pytest.mark.asyncio
async def test_remote_git_requires_explicit_egress_scope(monkeypatch) -> None:
    monkeypatch.setattr(settings, "git_egress_allowlist", "")
    monkeypatch.setattr(settings, "git_allowed_ip_cidrs", "")

    with pytest.raises(ValueError, match="egress"):
        await _git_egress_policy("https://git.example/repository.git")


@pytest.mark.asyncio
async def test_https_git_resolution_is_pinned_and_redirects_are_disabled(monkeypatch) -> None:
    async def resolve(hostname, port, networks):
        assert hostname == "git.example"
        assert port == 443
        assert ipaddress.ip_address("203.0.113.7") in networks[0]
        return (ipaddress.ip_address("203.0.113.7"),)

    monkeypatch.setattr(settings, "git_egress_allowlist", "git.example")
    monkeypatch.setattr(settings, "git_allowed_ip_cidrs", "203.0.113.0/24")
    monkeypatch.setattr("lode.infrastructure.git_source.resolve_checked_addresses", resolve)

    policy = await _git_egress_policy("https://git.example/repository.git")

    assert policy.git_options == (
        "-c",
        "http.followRedirects=false",
        "-c",
        "http.curloptResolve=git.example:443:203.0.113.7",
    )


@pytest.mark.asyncio
async def test_ssh_git_resolution_is_pinned_with_original_host_key(monkeypatch) -> None:
    async def resolve(_hostname, _port, _networks):
        return (ipaddress.ip_address("203.0.113.8"),)

    monkeypatch.setattr(settings, "git_egress_allowlist", "git.example")
    monkeypatch.setattr(settings, "git_allowed_ip_cidrs", "203.0.113.0/24")
    monkeypatch.setattr("lode.infrastructure.git_source.resolve_checked_addresses", resolve)

    policy = await _git_egress_policy("git@git.example:repository.git")

    assert policy.environment({})["GIT_SSH_COMMAND"] == (
        "ssh -o Hostname=203.0.113.8 -o HostKeyAlias=git.example"
    )
