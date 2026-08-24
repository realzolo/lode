"""Capability-limited collectors for externally managed service evidence.

The only external operations in this module are fixed status reads. Credentials
may be operational for Redis/Kafka, but neither connector exposes an API that
accepts a command or query supplied by a caller or an LLM. ClickHouse is more
restrictive: its effective grants must match a small allow-list before use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Protocol

from lode.config import settings
from lode.crypto import decrypt_secret
from lode.db.models.integration import ApplicationIntegration
from lode.db.models.intake import AuditEvent, EvidenceArtifact
from lode.engine.evidence.secret_mask import mask_secrets
from lode.integration_policy import (
    IntegrationPolicyError,
    assert_egress_allowed,
    normalize_integration_config,
)

_COLLECTOR_VERSION = "2"
_MAX_EXCERPT_CHARS = 20_000


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
    async def verify_readonly(self, config: dict[str, Any], credential: str) -> None: ...

    async def collect_snapshot(self, config: dict[str, Any], credential: str) -> Snapshot: ...


def resolve_integration_secret(stored_secret: str) -> str:
    credential = decrypt_secret(stored_secret)
    if not credential:
        raise IntegrationError("integration credential is missing")
    if credential.startswith("env://"):
        credential = os.environ.get(credential[len("env://") :], "")
    if not credential:
        raise IntegrationError("integration credential environment variable is not set")
    return credential


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
        assert_egress_allowed(normalized)
        return normalized
    except (IntegrationPolicyError, ValueError) as exc:
        raise ReadOnlyVerificationError(str(exc)) from exc


class RedisStatusClient:
    """The complete Redis capability surface available to collectors."""

    async def open(self, config: dict[str, Any], credential: str):
        try:
            import redis.asyncio as redis
        except ImportError as exc:  # pragma: no cover
            raise IntegrationError("redis is required for Redis integrations") from exc
        return redis.Redis(
            host=config["host"], port=config["port"], db=config["database"],
            username=config.get("username"), password=credential, ssl=True,
            ssl_cert_reqs="required", decode_responses=True, socket_timeout=8,
        )

    async def status(self, client) -> tuple[dict[str, Any], Any, Any]:
        # Typed redis-py methods only. This façade has no execute_command.
        return await client.info(), await client.role(), await client.latency_latest()


class RedisConnector:
    def __init__(self, client: RedisStatusClient | None = None):
        self._client = client or RedisStatusClient()

    async def verify_readonly(self, config: dict[str, Any], credential: str) -> None:
        config = _normalized_config("redis", config)
        client = await self._client.open(config, credential)
        try:
            await client.ping()
        except Exception as exc:
            raise IntegrationError(f"Redis status probe failed: {exc}") from exc
        finally:
            await client.aclose()

    async def collect_snapshot(self, config: dict[str, Any], credential: str) -> Snapshot:
        config = _normalized_config("redis", config)
        client = await self._client.open(config, credential)
        try:
            info, role, latency = await self._client.status(client)
        except Exception as exc:
            raise IntegrationError(f"Redis status collection failed: {exc}") from exc
        finally:
            await client.aclose()
        keys = (
            "redis_version", "role", "connected_clients", "used_memory",
            "used_memory_peak", "evicted_keys", "rejected_connections",
            "rdb_last_bgsave_status", "master_repl_offset", "master_link_status",
        )
        return Snapshot(
            "redis://status",
            "Redis role, replication, memory, persistence, connections, and latency inspected",
            _redacted_payload({"info": {key: info.get(key) for key in keys}, "role": role, "latency": latency}),
            str(info.get("master_repl_offset") or "") or None,
        )


class KafkaStatusClient:
    """The complete Kafka capability surface exposed to collectors."""

    async def _open(self, config: dict[str, Any], credential: str):
        from aiokafka.admin import AIOKafkaAdminClient

        client = AIOKafkaAdminClient(
            bootstrap_servers=config["bootstrap_servers"], security_protocol="SASL_SSL",
            sasl_mechanism=config["sasl_mechanism"], sasl_plain_username=config["username"],
            sasl_plain_password=credential,
        )
        await client.start()
        return client

    async def probe(self, config: dict[str, Any], credential: str) -> None:
        client = await self._open(config, credential)
        try:
            await client.describe_cluster()
        finally:
            await client.close()

    async def status(self, config: dict[str, Any], credential: str) -> tuple[Any, Any, Any, Any, list[dict[str, Any]]]:
        client = await self._open(config, credential)
        try:
            # The façade exposes only metadata reads. It never returns the
            # AIOKafkaAdminClient or accepts an operation/command argument.
            cluster = await client.describe_cluster()
            all_topics = await client.list_topics()
            descriptions = await client.describe_topics(config["topics"]) if config["topics"] else []
            groups = await client.list_consumer_groups()
            group_lag = await self._group_lag(config, credential, client, groups)
            return cluster, all_topics, descriptions, groups, group_lag
        finally:
            await client.close()

    async def _group_lag(self, config: dict[str, Any], credential: str, admin, groups: Any) -> list[dict[str, Any]]:
        """Read committed/end offsets without joining or consuming from a group."""
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError as exc:  # pragma: no cover
            raise IntegrationError("aiokafka is required for Kafka integrations") from exc
        group_ids = [str(group[0] if isinstance(group, tuple) else getattr(group, "group_id", group)) for group in groups][:20]
        committed: dict[str, dict[Any, Any]] = {}
        for group_id in group_ids:
            try:
                committed[group_id] = await admin.list_consumer_group_offsets(group_id)
            except Exception:
                committed[group_id] = {}
        partitions = list({partition for offsets in committed.values() for partition in offsets})[:200]
        if not partitions:
            return []
        consumer = AIOKafkaConsumer(
            bootstrap_servers=config["bootstrap_servers"], security_protocol="SASL_SSL",
            sasl_mechanism=config["sasl_mechanism"], sasl_plain_username=config["username"],
            sasl_plain_password=credential, enable_auto_commit=False,
        )
        try:
            await consumer.start()
            end_offsets = await consumer.end_offsets(partitions)
        except Exception:
            return []
        finally:
            await consumer.stop()
        summary: list[dict[str, Any]] = []
        for group_id, offsets in committed.items():
            lag = sum(max(0, int(end_offsets.get(partition, 0)) - int(getattr(offset, "offset", offset))) for partition, offset in offsets.items())
            summary.append({"group_id": group_id, "lag": lag, "partitions": len(offsets)})
        return sorted(summary, key=lambda item: item["lag"], reverse=True)


class KafkaConnector:
    def __init__(self, client: KafkaStatusClient | None = None):
        self._client = client or KafkaStatusClient()

    async def verify_readonly(self, config: dict[str, Any], credential: str) -> None:
        config = _normalized_config("kafka", config)
        try:
            await self._client.probe(config, credential)
        except IntegrationError:
            raise
        except Exception as exc:
            raise IntegrationError(f"Kafka metadata probe failed: {exc}") from exc

    async def collect_snapshot(self, config: dict[str, Any], credential: str) -> Snapshot:
        config = _normalized_config("kafka", config)
        try:
            cluster, all_topics, descriptions, groups, group_lag = await self._client.status(config, credential)
        except Exception as exc:
            raise IntegrationError(f"Kafka metadata collection failed: {exc}") from exc
        return Snapshot(
            "kafka://cluster",
            "Kafka cluster, brokers, topic partitions, replicas, ISR, and consumer-group inventory inspected",
            _redacted_payload({"cluster": cluster, "topic_count": len(all_topics), "topics": descriptions, "consumer_groups": groups, "consumer_group_lag": group_lag}),
            str(getattr(cluster, "cluster_id", "")) or None,
        )


def _verify_clickhouse_grants(grants: list[str], database: str) -> None:
    """Accept only SELECT grants on the configured DB and fixed system views."""
    allowed = {
        f"{database}.*", "system.replicas", "system.replication_queue",
        "system.errors", "system.metrics",
    }
    has_database_select = False
    for line in grants:
        normalized = " ".join(line.strip().replace("`", "").split())
        prefix = "GRANT SELECT ON "
        if not normalized.startswith(prefix) or " WITH GRANT OPTION" in normalized:
            raise ReadOnlyVerificationError("ClickHouse grant is outside the read-only allowlist")
        scope = normalized[len(prefix) :].split(" TO ", 1)[0]
        if scope not in allowed:
            raise ReadOnlyVerificationError(f"ClickHouse grant '{scope}' is outside the read-only allowlist")
        has_database_select = has_database_select or scope == f"{database}.*"
    if not has_database_select:
        raise ReadOnlyVerificationError("ClickHouse account lacks configured-database SELECT proof")


class ClickHouseConnector:
    _GRANTS = "SHOW GRANTS FOR CURRENT_USER"
    _REPLICAS = "SELECT database, table, is_readonly, queue_size, inserts_in_queue, merges_in_queue FROM system.replicas LIMIT 100"
    _QUEUE = "SELECT database, table, type, count() AS pending FROM system.replication_queue GROUP BY database, table, type ORDER BY pending DESC LIMIT 100"
    _ERRORS = "SELECT name, value FROM system.errors WHERE value > 0 ORDER BY value DESC LIMIT 50"
    _METRICS = "SELECT metric, value FROM system.metrics WHERE metric IN ('Query', 'TCPConnection') LIMIT 20"

    async def _query(self, config: dict[str, Any], credential: str, template: str) -> list[dict[str, Any]]:
        try:
            import clickhouse_connect
        except ImportError as exc:  # pragma: no cover
            raise IntegrationError("clickhouse-connect is required for ClickHouse integrations") from exc

        def run() -> list[dict[str, Any]]:
            client = clickhouse_connect.get_client(
                host=config["host"], port=config["port"], username=config["username"],
                password=credential, database=config["database"], secure=True,
                settings={"readonly": 2, "max_execution_time": 8, "max_result_rows": 200},
            )
            try:
                result = client.query(template)
                return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]
            finally:
                client.close()

        try:
            return await asyncio.to_thread(run)
        except Exception as exc:
            raise IntegrationError(f"ClickHouse fixed status query failed: {exc}") from exc

    async def verify_readonly(self, config: dict[str, Any], credential: str) -> None:
        config = _normalized_config("clickhouse", config)
        grants = await self._query(config, credential, self._GRANTS)
        _verify_clickhouse_grants([str(value) for row in grants for value in row.values()], config["database"])

    async def collect_snapshot(self, config: dict[str, Any], credential: str) -> Snapshot:
        config = _normalized_config("clickhouse", config)
        replicas, queue, errors, metrics = await asyncio.gather(
            self._query(config, credential, self._REPLICAS),
            self._query(config, credential, self._QUEUE),
            self._query(config, credential, self._ERRORS),
            self._query(config, credential, self._METRICS),
        )
        return Snapshot(
            "clickhouse://system",
            "ClickHouse replicas, replication queue, system errors, and metrics inspected",
            _redacted_payload({"replicas": replicas, "replication_queue": queue, "errors": errors, "metrics": metrics}),
        )


_CONNECTORS = MappingProxyType({"redis": RedisConnector(), "kafka": KafkaConnector(), "clickhouse": ClickHouseConnector()})


def connector_for(kind: str) -> IntegrationConnector:
    try:
        return _CONNECTORS[kind]
    except KeyError as exc:
        raise IntegrationError(f"unsupported integration kind '{kind}'") from exc


def _snapshot_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def collect_service_evidence(session, application_id: int, analysis_id: int) -> list[dict[str, Any]]:
    """Collect bounded, analysis-time observations without blocking other evidence."""
    from sqlalchemy import select

    rows = (await session.execute(
        select(ApplicationIntegration).where(ApplicationIntegration.application_id == application_id).where(ApplicationIntegration.state == "active")
    )).scalars().all()
    collected: list[dict[str, Any]] = []
    for integration in rows:
        started_at = datetime.now(UTC)
        try:
            config = normalize_integration_config(integration.kind, dict(integration.config or {}))
            credential = resolve_integration_secret(integration.secret_ref)
            connector = connector_for(integration.kind)
            await asyncio.wait_for(connector.verify_readonly(config, credential), timeout=settings.integration_collect_timeout_seconds)
            integration.readonly_verified_at = datetime.now(UTC)
            snapshot = await asyncio.wait_for(connector.collect_snapshot(config, credential), timeout=settings.integration_collect_timeout_seconds)
        except (ReadOnlyVerificationError, IntegrationPolicyError, ValueError) as exc:
            integration.state = "disabled"
            integration.last_error = f"policy verification failed: {exc}"[:1000]
            session.add(AuditEvent(
                action="integration.disable", target_type="integration", target_id=str(integration.id), application_id=application_id,
                result="error", detail={"reason": integration.last_error, "analysis_id": analysis_id},
            ))
            continue
        except (IntegrationError, asyncio.TimeoutError) as exc:
            integration.last_error = f"collection unavailable: {exc}"[:1000]
            continue
        except Exception as exc:  # fail closed for this artifact, not the incident workflow
            integration.last_error = f"collector failure: {type(exc).__name__}"[:1000]
            continue

        finished_at = datetime.now(UTC)
        payload = _redacted_payload(snapshot.payload)
        excerpt = json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True)[:_MAX_EXCERPT_CHARS]
        retention = None
        if settings.evidence_retention_days > 0:
            from datetime import timedelta
            retention = finished_at + timedelta(days=settings.evidence_retention_days)
        config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        artifact = EvidenceArtifact(
            analysis_id=analysis_id, artifact_type="service_snapshot", source_kind=integration.kind, source_id=integration.id,
            locator=f"{snapshot.locator}/integration/{integration.id}", content_hash=_snapshot_hash(payload), redacted_excerpt=excerpt,
            metadata_={
                "integration_name": integration.name, "summary": snapshot.summary, "collector_version": _COLLECTOR_VERSION,
                "config_hash": config_hash, "permission_verification": "passed",
                "observed_started_at": started_at.isoformat(), "observed_finished_at": finished_at.isoformat(),
                "time_scope": "analysis_time_observation", "source_position": snapshot.source_position,
            }, retention_until=retention,
        )
        session.add(artifact)
        await session.flush()
        integration.last_collected_at = finished_at
        integration.last_error = None
        collected.append({
            "artifact_id": artifact.id, "kind": integration.kind, "locator": artifact.locator, "summary": snapshot.summary,
            "excerpt": excerpt, "observed_started_at": started_at.isoformat(), "observed_finished_at": finished_at.isoformat(),
            "time_scope": "analysis_time_observation",
        })
    await session.flush()
    return collected
