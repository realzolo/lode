from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from lode.api.routes import control_plane
from lode.evidence_connectors.types import ProviderHTTPResponse


def response(payload: dict) -> ProviderHTTPResponse:
    return ProviderHTTPResponse(
        200,
        {"content-type": "application/json"},
        json.dumps(payload).encode(),
    )


@pytest.mark.asyncio
async def test_openai_model_discovery_uses_official_inventory_endpoint(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    async def request(_method, endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return response({"object": "list", "data": [{"id": "gpt-5.6-sol"}, {"id": "other"}]})

    monkeypatch.setattr(control_plane, "provider_request", request)

    result = await control_plane._discover_provider_models(
        provider_kind="openai",
        base_url="https://api.openai.com/v1",
        api_key="secret",
    )

    assert result == ("gpt-5.6-sol", "other")
    assert calls[0][0] == "https://api.openai.com/v1/models"
    assert calls[0][1]["headers"]["authorization"] == "Bearer secret"
    assert calls[0][1]["query"] is None


@pytest.mark.asyncio
async def test_anthropic_model_discovery_follows_bounded_after_id_pagination(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    pages = [
        response({"data": [{"id": "claude-sonnet-5"}], "has_more": True, "last_id": "page-1"}),
        response({"data": [{"id": "claude-haiku-4-5-20251001"}], "has_more": False}),
    ]

    async def request(_method, endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return pages.pop(0)

    monkeypatch.setattr(control_plane, "provider_request", request)

    result = await control_plane._discover_provider_models(
        provider_kind="anthropic",
        base_url="https://api.anthropic.com",
        api_key="secret",
    )

    assert result == ("claude-haiku-4-5-20251001", "claude-sonnet-5")
    assert [call[1]["query"] for call in calls] == [
        {"limit": "100"},
        {"limit": "100", "after_id": "page-1"},
    ]
    assert all(call[0] == "https://api.anthropic.com/v1/models" for call in calls)
    assert all(call[1]["headers"]["x-api-key"] == "secret" for call in calls)


@pytest.mark.asyncio
async def test_model_discovery_maps_invalid_pagination_to_stable_api_error(monkeypatch) -> None:
    async def request(*_args, **_kwargs):
        return response({"data": [{"id": "claude-sonnet-5"}], "has_more": True})

    monkeypatch.setattr(control_plane, "provider_request", request)

    with pytest.raises(HTTPException) as error:
        await control_plane._safe_discover(
            provider_kind="anthropic",
            protocol_id="anthropic.messages.v1",
            base_url="https://api.anthropic.com",
            api_key="secret",
        )

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "model_discovery_invalid_response"
    assert "secret" not in json.dumps(error.value.detail)
