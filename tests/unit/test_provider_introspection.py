"""Provider inventory protocol tests for the global model control plane."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from lode.api.routes import control_plane


class _Client:
    last_headers: dict[str, str] = {}

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        self.__class__.last_headers = headers
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"data": [{"id": "available-model", "owned_by": "provider"}]},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "organization", "project", "expected", "absent"),
    [
        (
            "anthropic",
            None,
            None,
            {"x-api-key": "provider-secret", "anthropic-version": "2023-06-01"},
            {"authorization"},
        ),
        (
            "openai",
            "org-current",
            "project-current",
            {
                "authorization": "Bearer provider-secret",
                "OpenAI-Organization": "org-current",
                "OpenAI-Project": "project-current",
            },
            {"x-api-key", "anthropic-version"},
        ),
    ],
)
async def test_provider_inventory_uses_provider_specific_authentication(
    monkeypatch,
    kind: str,
    organization: str | None,
    project: str | None,
    expected: dict[str, str],
    absent: set[str],
) -> None:
    monkeypatch.setattr(control_plane, "decrypt_secret", lambda _value: "provider-secret")
    monkeypatch.setattr(control_plane.httpx, "AsyncClient", _Client)
    provider = SimpleNamespace(
        provider_kind=kind,
        credential_ciphertext="encrypted",
        base_url="https://models.example.invalid/v1",
        organization_ref=organization,
        project_ref=project,
    )

    models = await control_plane._provider_models(provider)

    assert models == [{"id": "available-model", "owned_by": "provider"}]
    assert _Client.last_headers.items() >= expected.items()
    assert absent.isdisjoint(_Client.last_headers)
