"""Capability-limited integration verification and fixed snapshot collectors."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from lode.crypto import decrypt_secret
from lode.engine.db_proxy import verify_database_readonly
from lode.engine.evidence.secret_mask import mask_secrets
from lode.integration_policy import (
    IntegrationPolicyError,
    normalize_integration_config,
)


class IntegrationError(Exception):
    pass


class ReadOnlyVerificationError(IntegrationError):
    pass


@dataclass(frozen=True)
class Snapshot:
    locator: str
    summary: str
    payload: dict[str, Any]
    source_position: str | None = None


class IntegrationConnector(Protocol):
    async def verify_readonly(
        self, config: dict[str, Any], secrets: dict[str, str]
    ) -> None: ...

    async def collect_snapshot(
        self, config: dict[str, Any], secrets: dict[str, str]
    ) -> Snapshot: ...


def resolve_integration_secrets(ciphertext: str) -> dict[str, str]:
    plaintext = decrypt_secret(ciphertext)
    if not plaintext:
        raise IntegrationError("integration secrets are missing")
    try:
        value = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise IntegrationError("integration secret payload is invalid") from exc
    if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
        raise IntegrationError("integration secret payload is invalid")
    return {str(key): item for key, item in value.items()}


def _redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sensitive = ("password", "passwd", "secret", "token", "api_key", "access_key", "authorization")

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "***" if any(hint in str(key).lower() for hint in sensitive) else scrub(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [scrub(item) for item in value]
        return value

    text = json.dumps(scrub(payload), default=str, ensure_ascii=False, sort_keys=True)
    masked, _categories = mask_secrets(text)
    return json.loads(masked)


def _normalized_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = normalize_integration_config(kind, config)
        return normalized
    except (IntegrationPolicyError, ValueError) as exc:
        raise ReadOnlyVerificationError(str(exc)) from exc


class DatabaseConnector:
    async def verify_readonly(self, config: dict[str, Any], secrets: dict[str, str]) -> None:
        config = _normalized_config("database", config)
        try:
            await verify_database_readonly(config, secrets)
        except Exception as exc:
            raise ReadOnlyVerificationError(str(exc)) from exc

    async def collect_snapshot(self, config: dict[str, Any], secrets: dict[str, str]) -> Snapshot:
        raise IntegrationError("database integrations expose only the fixed query catalog")


class KafkaStatusClient:
    async def _open(self, config: dict[str, Any], secrets: dict[str, str]):
        from aiokafka.admin import AIOKafkaAdminClient

        client = AIOKafkaAdminClient(
            bootstrap_servers=config["bootstrap_servers"], security_protocol="SASL_SSL",
            sasl_mechanism=config["sasl_mechanism"],
            sasl_plain_username=config["username"], sasl_plain_password=secrets["password"],
        )
        await client.start()
        return client

    async def probe(self, config: dict[str, Any], secrets: dict[str, str]) -> None:
        client = await self._open(config, secrets)
        try:
            await client.describe_cluster()
            await client.describe_topics(config["topics"])
        finally:
            await client.close()

    async def status(
        self, config: dict[str, Any], secrets: dict[str, str]
    ) -> tuple[Any, Any, list[dict[str, Any]]]:
        client = await self._open(config, secrets)
        try:
            cluster = await client.describe_cluster()
            descriptions = await client.describe_topics(config["topics"])
            lag = await self._group_lag(config, secrets, client)
            return cluster, descriptions, lag
        finally:
            await client.close()

    async def _group_lag(
        self, config: dict[str, Any], secrets: dict[str, str], admin: Any
    ) -> list[dict[str, Any]]:
        from aiokafka import AIOKafkaConsumer

        committed: dict[str, dict[Any, Any]] = {}
        for group_id in config["consumer_groups"]:
            try:
                committed[group_id] = await admin.list_consumer_group_offsets(group_id)
            except Exception:
                committed[group_id] = {}
        partitions = [
            partition for offsets in committed.values() for partition in offsets
            if getattr(partition, "topic", None) in set(config["topics"])
        ][:200]
        if not partitions:
            return []
        consumer = AIOKafkaConsumer(
            bootstrap_servers=config["bootstrap_servers"], security_protocol="SASL_SSL",
            sasl_mechanism=config["sasl_mechanism"],
            sasl_plain_username=config["username"], sasl_plain_password=secrets["password"],
            enable_auto_commit=False,
        )
        try:
            await consumer.start()
            end_offsets = await consumer.end_offsets(partitions)
        finally:
            await consumer.stop()
        output: list[dict[str, Any]] = []
        for group_id, offsets in committed.items():
            scoped = {key: value for key, value in offsets.items() if key in partitions}
            lag = sum(
                max(0, int(end_offsets.get(partition, 0)) - int(getattr(offset, "offset", offset)))
                for partition, offset in scoped.items()
            )
            output.append({"group_id": group_id, "lag": lag, "partitions": len(scoped)})
        return sorted(output, key=lambda item: item["lag"], reverse=True)


class KafkaConnector:
    def __init__(self, client: KafkaStatusClient | None = None):
        self._client = client or KafkaStatusClient()

    async def verify_readonly(self, config: dict[str, Any], secrets: dict[str, str]) -> None:
        config = _normalized_config("kafka", config)
        try:
            await self._client.probe(config, secrets)
        except Exception as exc:
            raise IntegrationError(f"Kafka metadata probe failed: {exc}") from exc

    async def collect_snapshot(self, config: dict[str, Any], secrets: dict[str, str]) -> Snapshot:
        config = _normalized_config("kafka", config)
        try:
            cluster, descriptions, lag = await self._client.status(config, secrets)
        except Exception as exc:
            raise IntegrationError(f"Kafka metadata collection failed: {exc}") from exc
        return Snapshot(
            "kafka://configured-scope",
            "Kafka configured topics, replicas, ISR, and allowlisted consumer-group lag inspected",
            _redacted_payload({"cluster": cluster, "topics": descriptions, "consumer_group_lag": lag}),
            str(getattr(cluster, "cluster_id", "")) or None,
        )


class HttpConnector:
    def __init__(self, kind: str, readiness_path: str):
        self.kind = kind
        self.readiness_path = readiness_path

    async def verify_readonly(self, config: dict[str, Any], secrets: dict[str, str]) -> None:
        config = _normalized_config(self.kind, config)

        def probe() -> None:
            request = urllib.request.Request(
                f"{config['base_url']}{self.readiness_path}", method="GET",
                headers={"Authorization": f"Bearer {secrets['bearer_token']}"}
                if secrets.get("bearer_token") else {},
            )
            with urllib.request.urlopen(request, timeout=8, context=ssl.create_default_context()) as response:
                if response.status >= 400:
                    raise IntegrationError(f"{self.kind} readiness returned HTTP {response.status}")

        try:
            await asyncio.to_thread(probe)
        except Exception as exc:
            raise IntegrationError(f"{self.kind} readiness probe failed: {exc}") from exc

    async def collect_snapshot(self, config: dict[str, Any], secrets: dict[str, str]) -> Snapshot:
        if self.kind != "prometheus":
            raise IntegrationError(f"{self.kind} uses the log-search capability")
        config = _normalized_config("prometheus", config)

        def query() -> dict[str, Any]:
            url = f"{config['base_url']}/api/v1/query?{urllib.parse.urlencode({'query': config['query']})}"
            request = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {secrets['bearer_token']}"}
                if secrets.get("bearer_token") else {},
            )
            with urllib.request.urlopen(request, timeout=10, context=ssl.create_default_context()) as response:
                return json.loads(response.read(200_000).decode("utf-8"))

        payload = await asyncio.to_thread(query)
        return Snapshot("prometheus://query", "Prometheus administrator-defined fixed query inspected", _redacted_payload(payload))


_CONNECTORS = MappingProxyType(
    {
        "database": DatabaseConnector(), "kafka": KafkaConnector(),
        "loki": HttpConnector("loki", "/ready"),
        "prometheus": HttpConnector("prometheus", "/-/ready"),
    }
)


def connector_for(kind: str) -> IntegrationConnector:
    try:
        return _CONNECTORS[kind]
    except KeyError as exc:
        raise IntegrationError(f"integration kind '{kind}' has no connector") from exc
