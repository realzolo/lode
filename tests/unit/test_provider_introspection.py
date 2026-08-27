"""Provider inventory protocol tests for the global model control plane."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lode.api.routes import control_plane
from lode.evidence_connectors.types import ProviderHTTPResponse


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
    captured: dict[str, object] = {}

    async def request(method, endpoint, **kwargs):
        captured.update(method=method, endpoint=endpoint, headers=kwargs["headers"])
        return ProviderHTTPResponse(
            200,
            {"content-type": "application/json"},
            json.dumps({"data": [{"id": "available-model", "owned_by": "provider"}]}).encode(),
        )

    monkeypatch.setattr(control_plane, "decrypt_secret", lambda _value: "provider-secret")
    monkeypatch.setattr(control_plane, "provider_request", request)
    provider = SimpleNamespace(
        provider_kind=kind,
        credential_ciphertext="encrypted",
        base_url="https://models.example.invalid/v1",
        organization_ref=organization,
        project_ref=project,
    )

    models = await control_plane._provider_models(provider)

    assert models == [{"id": "available-model", "owned_by": "provider"}]
    assert captured["method"] == "GET"
    assert captured["endpoint"] == "https://models.example.invalid/v1/models"
    assert captured["headers"].items() >= expected.items()
    assert absent.isdisjoint(captured["headers"])
