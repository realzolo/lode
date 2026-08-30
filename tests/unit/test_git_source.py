from __future__ import annotations

from types import SimpleNamespace

import pytest

from lode.db.models import GitRepository
from lode.infrastructure.git_source import (
    GitRemoteRevisionResolver,
    validate_git_remote,
)
from lode.infrastructure.source_executor import (
    _normalize_values,
    _repository_aliases,
    _selected_repository_is_relevant,
    _source_query_fingerprint,
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


def test_source_query_fingerprint_reuses_only_the_same_normalized_query() -> None:
    common = {
        "symbols": ("createOrder",),
        "path_hints": (),
        "evidence_refs": (101,),
    }
    first = _source_query_fingerprint(
        repository_snapshot_id=7,
        revision="a" * 40,
        source_query={**common, "terms": _normalize_values(("result_code", "Payssion"))},
    )
    reordered = _source_query_fingerprint(
        repository_snapshot_id=7,
        revision="a" * 40,
        source_query={**common, "terms": _normalize_values(("Payssion", "result_code"))},
    )
    follow_up = _source_query_fingerprint(
        repository_snapshot_id=7,
        revision="a" * 40,
        source_query={
            **common,
            "terms": _normalize_values(("Payssion", "pm_id", "result_code")),
        },
    )

    assert first == reordered
    assert follow_up != first


def test_runtime_repository_identity_rejects_an_unrelated_source_repository() -> None:
    gateway = GitRepository(
        adapter_id="github",
        endpoint_identity_hash="a" * 64,
        external_repository_id="gateway",
        name="aidol-payment-gateway",
        full_name="ai-dol-team/aidol-payment-gateway",
        repo_url="https://github.example/ai-dol-team/aidol-payment-gateway.git",
        web_url="https://github.example/ai-dol-team/aidol-payment-gateway",
        visibility="private",
    )
    pixel_morph = GitRepository(
        adapter_id="github",
        endpoint_identity_hash="a" * 64,
        external_repository_id="pixel-morph",
        name="pixel-morph",
        full_name="ai-dol-team/pixel-morph",
        repo_url="https://github.example/ai-dol-team/pixel-morph.git",
        web_url="https://github.example/ai-dol-team/pixel-morph",
        visibility="private",
    )
    snapshots = (
        (SimpleNamespace(id=11), gateway),
        (SimpleNamespace(id=12), pixel_morph),
    )
    runtime_evidence = "loki labels app=payment-gateway service=payment-gateway"

    assert "payment-gateway" in _repository_aliases(gateway)
    assert _selected_repository_is_relevant(11, snapshots, runtime_evidence)
    assert not _selected_repository_is_relevant(12, snapshots, runtime_evidence)
