"""Frozen current business API surface and public secret-redaction contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lode.api.main import app


def test_frozen_business_endpoints_are_present() -> None:
    contract = json.loads(
        (Path(__file__).parents[2] / "contracts/v1/api/endpoints.json").read_text(encoding="utf-8")
    )
    expected = {tuple(item) for item in contract["endpoints"]}
    actual = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert expected <= actual


def test_control_plane_responses_never_publish_secret_values() -> None:
    schemas = app.openapi()["components"]["schemas"]
    provider_fields = set(schemas["ProviderAccountOut"]["properties"])
    connector_fields = set(schemas["ConnectorOut"]["properties"])

    assert "credential" not in provider_fields
    assert "credential_ciphertext" not in provider_fields
    assert "api_key" not in provider_fields
    assert "api_key_ciphertext" not in provider_fields
    assert "secrets" not in connector_fields
    assert "secret_ciphertext" not in connector_fields
    assert "configured_secret_fields" in connector_fields


@pytest.mark.asyncio
async def test_validation_errors_identify_the_first_invalid_field_without_echoing_input() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            json={"username": "x", "password": "must-not-be-echoed"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["message"].startswith("Invalid username:")
    assert "must-not-be-echoed" not in response.text
