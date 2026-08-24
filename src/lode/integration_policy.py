"""Type and network policy for externally managed service integrations.

This module is deliberately independent from HTTP routing and connector code:
both control-plane validation and worker-time collection apply the same policy.
"""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lode.config import settings

_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


class IntegrationPolicyError(ValueError):
    pass


class _IntegrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RedisIntegrationConfig(_IntegrationConfig):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=6380, ge=1, le=65535)
    tls: Literal[True] = True
    username: str | None = Field(default=None, min_length=1, max_length=200)
    database: int = Field(default=0, ge=0, le=15)


class KafkaIntegrationConfig(_IntegrationConfig):
    bootstrap_servers: list[str] = Field(min_length=1, max_length=20)
    security_protocol: Literal["SASL_SSL"] = "SASL_SSL"
    sasl_mechanism: Literal["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"] = "PLAIN"
    username: str = Field(min_length=1, max_length=200)
    topics: list[str] = Field(default_factory=list, max_length=20)


class ClickHouseIntegrationConfig(_IntegrationConfig):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=8443, ge=1, le=65535)
    database: str = Field(default="default", min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    tls: Literal[True] = True


def _bootstrap_host(value: str) -> str:
    host, separator, port = value.rpartition(":")
    if not separator or not host or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise IntegrationPolicyError("Kafka bootstrap servers must be DNS-host:port")
    return host


def normalize_integration_config(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    models: dict[str, type[_IntegrationConfig]] = {
        "redis": RedisIntegrationConfig,
        "kafka": KafkaIntegrationConfig,
        "clickhouse": ClickHouseIntegrationConfig,
    }
    try:
        data = models[kind].model_validate(value).model_dump(mode="json")
    except KeyError as exc:
        raise IntegrationPolicyError("unsupported integration kind") from exc
    hosts = [data["host"]] if "host" in data else [_bootstrap_host(item) for item in data["bootstrap_servers"]]
    def is_dns_name(host: str) -> bool:
        try:
            ip_address(host)
            return False
        except ValueError:
            return bool(_HOSTNAME.fullmatch(host))

    if any(not is_dns_name(host) for host in hosts):
        raise IntegrationPolicyError("integration endpoints must be DNS hostnames, not URLs or IP literals")
    return data


def assert_egress_allowed(config: dict[str, Any]) -> None:
    """Fail closed unless each endpoint is explicitly routed by policy.

    DNS names, instead of URL/IP inputs, keep endpoint selection in the
    control-plane. Production egress must additionally enforce this list at
    the network boundary; this check prevents accidental broadening in code.
    """
    configured = [entry.strip().lower() for entry in settings.integration_egress_allowlist.split(",") if entry.strip()]
    if not configured:
        raise IntegrationPolicyError("no integration egress allowlist is configured")
    hosts = [config["host"]] if "host" in config else [_bootstrap_host(item) for item in config["bootstrap_servers"]]
    for host in hosts:
        lowered = host.lower()
        if not any(
            lowered == rule or (rule.startswith("*.") and lowered.endswith(rule[1:]))
            for rule in configured
        ):
            raise IntegrationPolicyError(f"endpoint '{host}' is not in the integration egress allowlist")
