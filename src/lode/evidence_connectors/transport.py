"""Bounded HTTPS transport with DNS and redirect policy."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpcore
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
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("provider base_url must use a DNS hostname")
    if parsed_port is not None and parsed_port <= 0:
        raise ValueError("provider base_url port is invalid")
    origin = f"https://{parsed.hostname.lower()}"
    if parsed_port is not None and parsed_port != 443:
        origin += f":{parsed_port}"
    return origin, parsed.hostname.lower()


def validate_ip_cidrs(values: list[str]) -> list[str]:
    try:
        networks = [ipaddress.ip_network(item, strict=True) for item in values]
    except (TypeError, ValueError) as exc:
        raise ValueError("provider allowed_ip_cidrs contains an invalid network") from exc
    if not networks:
        raise ValueError("provider allowed_ip_cidrs must not be empty")
    return [str(network) for network in networks]


def validate_dns_hostname(hostname: str) -> str:
    return validate_base_url(f"https://{hostname}")[1]


async def resolve_checked_addresses(
    hostname: str,
    port: int,
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    backend = PinnedDNSBackend(hostname=hostname, port=port, networks=networks)
    return await backend._resolve()


class PinnedDNSBackend(httpcore.AnyIOBackend):
    """Resolve once per connection and connect only to the checked addresses."""

    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
    ) -> None:
        self.hostname = hostname
        self.port = port
        self.networks = networks

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.lower() != self.hostname or port != self.port:
            raise ProviderExecutionError(
                "egress_violation", "provider connection target changed after validation"
            )
        addresses = await self._resolve()
        failure: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in addresses:
            try:
                return await super().connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failure = exc
        if failure is not None:
            raise failure
        raise ProviderExecutionError("egress_violation", "provider DNS returned no addresses")

    async def _resolve(self) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo,
                self.hostname,
                self.port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ProviderExecutionError(
                "egress_violation", "provider DNS resolution failed"
            ) from exc
        addresses = tuple(
            sorted(
                {ipaddress.ip_address(item[4][0]) for item in records},
                key=lambda address: (address.version, int(address)),
            )
        )
        if not addresses or len(addresses) > 20:
            raise ProviderExecutionError(
                "egress_violation", "provider DNS returned an invalid address set"
            )
        for address in addresses:
            if (
                address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
            ):
                raise ProviderExecutionError(
                    "egress_violation", "provider DNS resolved to a forbidden address"
                )
            if not any(address in network for network in self.networks):
                raise ProviderExecutionError(
                    "egress_violation", "provider DNS address is outside egress scope"
                )
        return addresses


class PinnedHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: PinnedDNSBackend) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=backend,
        )


class BoundedHTTPTransport:
    def __init__(
        self,
        *,
        base_url: str,
        allowed_ip_cidrs: list[str],
        headers: Mapping[str, str],
        max_response_bytes: int,
        max_decompression_ratio: int = 20,
        max_timeout_ms: int = 60_000,
    ) -> None:
        self.base_url, self.hostname = validate_base_url(base_url)
        self.port = urlsplit(self.base_url).port or 443
        if not 1 <= max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("provider max_response_bytes is invalid")
        self.networks = tuple(
            ipaddress.ip_network(item) for item in validate_ip_cidrs(allowed_ip_cidrs)
        )
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
            raise ProviderExecutionError("egress_violation", "provider HTTP method is disabled")
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path
            or "?" in path
            or "#" in path
        ):
            raise ProviderExecutionError("egress_violation", "provider request path is invalid")
        if isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= self.max_timeout_ms:
            raise ProviderExecutionError("cost_exceeded", "provider timeout is invalid")
        backend = PinnedDNSBackend(
            hostname=self.hostname,
            port=self.port,
            networks=self.networks,
        )
        try:
            async with httpx.AsyncClient(  # noqa: SIM117 - stream context depends on this client
                headers=self.headers,
                follow_redirects=False,
                timeout=timeout_ms / 1000,
                verify=True,
                transport=PinnedHTTPTransport(backend),
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
                            "egress_violation", "provider redirect is disabled"
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
