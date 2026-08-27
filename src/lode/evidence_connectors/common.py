"""Shared provider HTTP response and credential primitives."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from lode.config import settings
from lode.evidence_connectors.types import (
    ProviderExecutionError,
    ProviderHTTPResponse,
    decode_provider_json,
)


def credential_identity_hash(secrets: Mapping[str, str]) -> str:
    if not secrets or any(
        not isinstance(key, str) or not isinstance(value, str) or not value
        for key, value in secrets.items()
    ):
        raise ValueError("provider secrets are invalid")
    payload = "\0".join(f"{key}\0{secrets[key]}" for key in sorted(secrets)).encode()
    return hmac.new(settings.credential_identity_key.encode(), payload, hashlib.sha256).hexdigest()


def provider_headers(secrets: Mapping[str, str]) -> dict[str, str]:
    supplied = set(secrets)
    if supplied == {"api_key"}:
        authorization = f"ApiKey {secrets['api_key']}"
    elif supplied == {"bearer_token"}:
        authorization = f"Bearer {secrets['bearer_token']}"
    elif supplied == {"username", "password"}:
        import base64

        value = base64.b64encode(f"{secrets['username']}:{secrets['password']}".encode()).decode()
        authorization = f"Basic {value}"
    else:
        raise ValueError("provider credentials must use exactly one supported authentication form")
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": authorization,
    }


def classify_response(response: ProviderHTTPResponse) -> None:
    status = response.status_code
    if status in {401, 403}:
        raise ProviderExecutionError(
            "authentication_failed", "provider rejected the read credential"
        )
    if status == 429:
        raise ProviderExecutionError("rate_limited", "provider rate limit was reached")
    if status in {408, 504}:
        raise ProviderExecutionError("provider_timeout", "provider timed out")
    if status >= 500:
        raise ProviderExecutionError("provider_unavailable", "provider is unavailable")
    if not 200 <= status < 300:
        raise ProviderExecutionError(
            "invalid_response",
            "provider rejected the request",
            {"status_code": status},
        )


def response_json(response: ProviderHTTPResponse) -> Any:
    classify_response(response)
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"application/json", "application/vnd.elasticsearch+json"}:
        raise ProviderExecutionError(
            "invalid_response", "provider response content type is invalid"
        )
    return decode_provider_json(response.body)
