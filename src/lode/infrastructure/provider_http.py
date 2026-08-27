"""Bounded, DNS-pinned HTTP adapter for AI provider traffic."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from lode.config import settings
from lode.evidence_connectors.transport import BoundedHTTPTransport, validate_dns_hostname
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
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("AI provider endpoint must use a DNS hostname")
    netloc = parsed.hostname.lower()
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
    """Execute one request after exact host and resolved-address authorization."""
    try:
        canonical = validate_provider_endpoint(endpoint)
        parsed = urlsplit(canonical)
        allowed_hosts = {
            validate_dns_hostname(item.strip())
            for item in settings.ai_provider_egress_allowlist.split(",")
            if item.strip()
        }
        if parsed.hostname not in allowed_hosts:
            raise ValueError("AI provider hostname is outside the configured egress allowlist")
        allowed_cidrs = [
            item.strip()
            for item in settings.ai_provider_allowed_ip_cidrs.split(",")
            if item.strip()
        ]
        origin = f"https://{parsed.hostname}"
        if parsed.port is not None and parsed.port != 443:
            origin += f":{parsed.port}"
        transport = BoundedHTTPTransport(
            base_url=origin,
            allowed_ip_cidrs=allowed_cidrs,
            headers=headers,
            max_response_bytes=settings.ai_provider_max_response_bytes,
            max_timeout_ms=300_000,
        )
    except ValueError as exc:
        raise ProviderExecutionError(
            "egress_violation", "AI provider outbound policy rejected the request"
        ) from exc
    return await transport.request(
        method,
        parsed.path or "/",
        json_body=json_body,
        timeout_ms=max(1, int(timeout_seconds * 1_000)),
    )
