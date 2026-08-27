"""Provider inventory protocol tests for the global model control plane."""

from __future__ import annotations

import json

import pytest

from lode.api.routes import control_plane
from lode.evidence_connectors.types import ProviderHTTPResponse


@pytest.mark.asyncio
async def test_provider_inventory_uses_openai_compatible_authentication(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def request(method, endpoint, **kwargs):
        captured.update(method=method, endpoint=endpoint, headers=kwargs["headers"])
        return ProviderHTTPResponse(
            200,
            {"content-type": "application/json"},
            json.dumps({"data": [{"id": "gpt-5.6-sol", "owned_by": "provider"}]}).encode(),
        )

    monkeypatch.setattr(control_plane, "provider_request", request)
    models = await control_plane._discover_provider_models(
        base_url="https://models.example.invalid/v1",
        credential="provider-secret",
        organization_ref="org-current",
        project_ref="project-current",
    )

    assert models.ids == {"gpt-5.6-sol"}
    assert captured["method"] == "GET"
    assert captured["endpoint"] == "https://models.example.invalid/v1/models"
    assert captured["headers"].items() >= {
        "authorization": "Bearer provider-secret",
        "OpenAI-Organization": "org-current",
        "OpenAI-Project": "project-current",
    }.items()
    assert "x-api-key" not in captured["headers"]
