from __future__ import annotations

import pytest

from lode.git_accounts.providers import endpoint_identity_hash, resolve_api_url


def test_registered_git_adapters_enforce_endpoint_policy() -> None:
    assert resolve_api_url("github", None) == "https://api.github.com"
    assert resolve_api_url("gitlab", "https://gitlab.internal/api/v4") == "https://gitlab.internal/api/v4"
    with pytest.raises(ValueError):
        resolve_api_url("gitee", "https://gitee.internal/api/v5")
    with pytest.raises(ValueError):
        resolve_api_url("gitea", "https://gitea.internal/api/v1")


def test_endpoint_identity_is_adapter_scoped() -> None:
    github = endpoint_identity_hash("github", "https://git.example/api/v4")
    gitlab = endpoint_identity_hash("gitlab", "https://git.example/api/v4")
    assert len(github) == 64
    assert github != gitlab
