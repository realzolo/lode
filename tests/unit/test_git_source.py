from __future__ import annotations

import pytest

from lode.infrastructure.git_source import (
    GitRemoteRevisionResolver,
    validate_git_remote,
)


@pytest.mark.parametrize(
    "url",
    (
        "https://github.com/example/repository.git",
        "https://gitlab.example.com/group/repository.git",
        "https://gitee.com/example/repository.git",
    ),
)
def test_git_source_accepts_only_canonical_https_remotes(url: str) -> None:
    validate_git_remote(url)


@pytest.mark.parametrize(
    "url",
    (
        "http://git.example/repository.git",
        "git@github.com:example/repository.git",
        "ssh://git@github.com/example/repository.git",
        "file:///tmp/repository",
        "/tmp/repository",
        "https://token@github.com/example/repository.git",
    ),
)
def test_git_source_rejects_non_catalog_remote_forms(url: str) -> None:
    with pytest.raises(ValueError):
        validate_git_remote(url)


@pytest.mark.asyncio
async def test_git_source_rejects_unapproved_remote_schemes() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        await GitRemoteRevisionResolver().resolve_branch(
            repo_url="http://git.example/repository.git",
            branch="main",
            credential=None,
        )
