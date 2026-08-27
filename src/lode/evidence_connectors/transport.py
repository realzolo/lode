"""Bounded HTTPS transport for provider and connector requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from lode.evidence_connectors.types import ProviderExecutionError, ProviderHTTPResponse


def validate_base_url(base_url: str) -> tuple[str, str]:
    parsed = urlsplit(base_url)
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ValueError("provider base_url port is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.netloc != parsed.netloc.lower()
    ):
        raise ValueError("provider base_url must be a credential-free HTTPS origin")
    if parsed_port is not None and parsed_port <= 0:
        raise ValueError("provider base_url port is invalid")
    hostname = parsed.hostname.lower()
    origin = f"https://[{hostname}]" if ":" in hostname else f"https://{hostname}"
    if parsed_port is not None and parsed_port != 443:
        origin += f":{parsed_port}"
    return origin, hostname


class BoundedHTTPTransport:
    def __init__(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str],
        max_response_bytes: int,
        max_decompression_ratio: int = 20,
        max_timeout_ms: int = 60_000,
    ) -> None:
        self.base_url, self.hostname = validate_base_url(base_url)
        self.port = urlsplit(self.base_url).port or 443
        if not 1 <= max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("provider max_response_bytes is invalid")
        self.headers = dict(headers)
        self.max_response_bytes = max_response_bytes
        if not 1 <= max_timeout_ms <= 300_000:
            raise ValueError("provider maximum timeout is invalid")
        self.max_timeout_ms = max_timeout_ms
        if not 1 <= max_decompression_ratio <= 100:
            raise ValueError("provider max_decompression_ratio is invalid")
        self.max_decompression_ratio = max_decompression_ratio

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout_ms: int,
    ) -> ProviderHTTPResponse:
        if method not in {"GET", "HEAD", "POST"}:
            raise ProviderExecutionError("invalid_response", "provider HTTP method is disabled")
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path
            or "?" in path
            or "#" in path
        ):
            raise ProviderExecutionError("invalid_response", "provider request path is invalid")
        if isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= self.max_timeout_ms:
            raise ProviderExecutionError("cost_exceeded", "provider timeout is invalid")
        try:
            async with httpx.AsyncClient(  # noqa: SIM117 - stream context depends on this client
                headers=self.headers,
                follow_redirects=False,
                timeout=timeout_ms / 1000,
                verify=True,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method,
                    self.base_url + path,
                    params=query,
                    json=json_body,
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise ProviderExecutionError(
                            "invalid_response", "provider redirect is disabled"
                        )
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    if encoding not in {"identity", "gzip", "deflate", "br"}:
                        raise ProviderExecutionError(
                            "invalid_response", "provider response encoding is disabled"
                        )
                    declared = response.headers.get("content-length")
                    compressed_size = int(declared) if declared and declared.isdigit() else None
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_response_bytes:
                            raise ProviderExecutionError(
                                "cost_exceeded", "provider response byte budget exceeded"
                            )
                        if (
                            encoding != "identity"
                            and compressed_size is not None
                            and size > max(1, compressed_size) * self.max_decompression_ratio
                        ):
                            raise ProviderExecutionError(
                                "cost_exceeded", "provider decompression ratio budget exceeded"
                            )
                        chunks.append(chunk)
                    return ProviderHTTPResponse(
                        status_code=response.status_code,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        body=b"".join(chunks),
                    )
        except ProviderExecutionError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderExecutionError("provider_timeout", "provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderExecutionError("provider_unavailable", "provider request failed") from exc
