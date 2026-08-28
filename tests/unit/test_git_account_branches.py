"""Branch catalogue behavior across supported Git providers."""

from __future__ import annotations

import json

import pytest

from lode.git_accounts import providers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_id", "external_id", "full_name", "expected_path", "expected_query"),
    [
        ("github", "101", "group/repository", "/repos/group/repository/branches", {"per_page": "100", "page": "2"}),
        ("gitlab", "group/repository", "group/repository", "/projects/group%2Frepository/repository/branches", {"per_page": "100", "page": "2", "search": "release"}),
        ("gitee", "101", "group/repository", "/repos/group/repository/branches", {"per_page": "100", "page": "2"}),
    ],
)
async def test_branch_catalogue_is_paged_and_searchable_for_each_provider(
    monkeypatch,
    adapter_id: str,
    external_id: str,
    full_name: str,
    expected_path: str,
    expected_query: dict[str, str],
) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def request(_adapter_id, _api_url, _method, path, *, token, query=None):
        assert token == "sensitive-token"
        calls.append((path, query))
        return json.dumps(
            [{"name": "release/2026"}] + [{"name": f"branch-{index}"} for index in range(99)]
        ).encode()

    monkeypatch.setattr(providers, "_request", request)

    branches, next_cursor = await providers.list_branches(
        adapter_id=adapter_id,
        api_url="https://git.example/api",
        token="sensitive-token",
        external_repository_id=external_id,
        full_name=full_name,
        page=2,
        query="release",
    )

    assert calls == [(expected_path, expected_query)]
    assert next_cursor == "3"
    if adapter_id == "gitlab":
        assert len(branches) == 100
        assert branches[0].name == "release/2026"
    else:
        assert [branch.name for branch in branches] == ["release/2026"]


@pytest.mark.asyncio
async def test_missing_branch_is_not_reported_as_a_provider_error_or_token_leak(monkeypatch) -> None:
    async def request(*_args, **_kwargs):
        raise providers.GitProviderError("not_found", "token=sensitive-token")

    monkeypatch.setattr(providers, "_request", request)

    exists = await providers.verify_branch(
        adapter_id="github",
        api_url="https://git.example/api",
        token="sensitive-token",
        external_repository_id="101",
        full_name="group/repository",
        branch_name="removed-branch",
    )

    assert exists is False
