"""Bounded HTTP adapter for AI provider traffic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from lode.runtime_defaults import AI_PROVIDER_MAX_RESPONSE_BYTES
from lode.evidence_connectors.transport import BoundedHTTPTransport
from lode.evidence_connectors.types import ProviderExecutionError, ProviderHTTPResponse


def validate_provider_endpoint(value: str) -> str:
    """Return a canonical HTTPS provider URL without accepting URL credentials."""
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("AI provider endpoint port is invalid") from exc
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc != parsed.netloc.lower()
        or (port is not None and port < 1)
        or (path and (not path.startswith("/") or path.startswith("//") or ".." in path))
    ):
        raise ValueError("AI provider endpoint must be a canonical credential-free HTTPS URL")
    hostname = parsed.hostname.lower()
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != 443:
        netloc += f":{port}"
    return urlunsplit(("https", netloc, path, "", ""))


def provider_endpoint(base_url: str, path: str) -> str:
    """Append a provider API path to a validated account base URL."""
    base = validate_provider_endpoint(base_url)
    parsed = urlsplit(base)
    suffix = path if path.startswith("/") else f"/{path}"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + suffix, "", ""))


async def provider_request(
    method: str,
    endpoint: str,
    *,
    headers: Mapping[str, str],
    timeout_seconds: float,
    json_body: Mapping[str, Any] | None = None,
) -> ProviderHTTPResponse:
    """Execute one bounded request to the provider account endpoint."""
    try:
        canonical = validate_provider_endpoint(endpoint)
        parsed = urlsplit(canonical)
        hostname = parsed.hostname
        origin = f"https://[{hostname}]" if ":" in hostname else f"https://{hostname}"
        if parsed.port is not None and parsed.port != 443:
            origin += f":{parsed.port}"
        transport = BoundedHTTPTransport(
            base_url=origin,
            headers=headers,
            max_response_bytes=AI_PROVIDER_MAX_RESPONSE_BYTES,
            max_timeout_ms=300_000,
        )
    except ValueError as exc:
        raise ProviderExecutionError(
            "invalid_response", "AI provider endpoint is invalid"
        ) from exc
    return await transport.request(
        method,
        parsed.path or "/",
        json_body=json_body,
        timeout_ms=max(1, int(timeout_seconds * 1_000)),
    )
