"""AI provider endpoint and bounded transport tests."""

from __future__ import annotations

import pytest

from lode.evidence_connectors.types import ProviderHTTPResponse
from lode.infrastructure import provider_http


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://models.example",
        "https://user:secret@models.example",
        "https://models.example/v1?target=internal",
        "https://MODELS.example/v1",
    ],
)
def test_provider_endpoint_rejects_noncanonical_or_unsafe_urls(endpoint: str) -> None:
    with pytest.raises(ValueError):
        provider_http.validate_provider_endpoint(endpoint)


@pytest.mark.asyncio
async def test_provider_request_delegates_to_any_valid_provider_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Transport:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def request(self, method, path, **kwargs):
            captured.update(method=method, path=path, request=kwargs)
            return ProviderHTTPResponse(200, {}, b"{}")

    monkeypatch.setattr(provider_http, "BoundedHTTPTransport", Transport)

    result = await provider_http.provider_request(
        "POST",
        "https://models.example/v1/chat/completions",
        headers={"authorization": "masked-in-test"},
        timeout_seconds=120,
        json_body={"model": "test"},
    )

    assert result.status_code == 200
    assert captured["base_url"] == "https://models.example"
    assert captured["max_timeout_ms"] == 300_000
    assert captured["path"] == "/v1/chat/completions"
    assert captured["request"] == {
        "json_body": {"model": "test"},
        "timeout_ms": 120_000,
    }


def test_provider_endpoint_allows_ip_literal() -> None:
    assert provider_http.validate_provider_endpoint("https://127.0.0.1/v1") == "https://127.0.0.1/v1"
