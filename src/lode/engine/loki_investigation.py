"""Bounded, server-generated Loki investigation waves."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from lode.db.models.integration import ApplicationIntegration
from lode.engine.integrations import resolve_integration_secrets
from lode.db.models.investigation import (
    EvidenceArtifact,
    EvidenceCollection,
    Investigation,
    InvestigationServiceSnapshot,
    InvestigationStep,
)
from lode.engine.evidence.secret_mask import mask_secrets
from lode.engine.investigation_events import finish_operation, start_operations


MAX_WAVE_CONCURRENCY = 4
IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
REQUEST_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
CORRELATION_KEYS = (
    "order_id",
    "job_id",
    "delivery_id",
    "provider_transaction_id",
)


@dataclass(frozen=True)
class LokiQuery:
    service_id: int
    service_name: str
    query: str
    phase: str
    correlation_key: str | None = None
    correlation_value: str | None = None


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def build_request_logql(
    *, service_name: str, environment: str, request_id: str
) -> str:
    for name, value in {
        "service_name": service_name,
        "environment": environment,
    }.items():
        if not IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {name}")
    if not REQUEST_ID.fullmatch(request_id):
        raise ValueError("invalid request_id")
    return (
        f'{{service_name={_quoted(service_name)}, environment={_quoted(environment)}}}'
        f' | json | request_id={_quoted(request_id)}'
    )


def build_lifecycle_logql(
    *,
    service_name: str,
    environment: str,
    correlation_key: str,
    correlation_value: str,
) -> str:
    if correlation_key not in CORRELATION_KEYS:
        raise ValueError("unsupported correlation key")
    if not correlation_value or len(correlation_value) > 500:
        raise ValueError("invalid correlation value")
    for name, value in {"service_name": service_name, "environment": environment}.items():
        if not IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {name}")
    base = f'{{service_name={_quoted(service_name)}, environment={_quoted(environment)}}} | json'
    return f"{base} | {correlation_key}={_quoted(correlation_value)}"


def _http_json(url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    import urllib.request

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _json_objects(value: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if isinstance(value, dict):
        objects.append(value)
        for child in value.values():
            objects.extend(_json_objects(child))
    elif isinstance(value, list):
        for child in value:
            objects.extend(_json_objects(child))
    elif isinstance(value, str) and value.startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return objects
        objects.extend(_json_objects(parsed))
    return objects


def _discovered(
    payloads: list[dict[str, Any]], *, default_service: str | None = None
) -> tuple[set[str], dict[str, str], dict[str, set[str]]]:
    peers: set[str] = set()
    correlation: dict[str, str] = {}
    commits: dict[str, set[str]] = {}
    for payload in payloads:
        for item in _json_objects(payload):
            peer = item.get("peer_service")
            if isinstance(peer, str):
                peers.add(peer)
            service = item.get("service_name")
            commit = item.get("git_commit")
            if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
                commit_service = service if isinstance(service, str) else default_service
                if commit_service:
                    commits.setdefault(commit_service, set()).add(commit)
            for key in CORRELATION_KEYS:
                value = item.get(key)
                if key not in correlation and isinstance(value, str) and value:
                    correlation[key] = value[:500]
    return peers, correlation, commits


def _fingerprint(
    item: LokiQuery,
    *,
    connector_id: int,
    connector_revision: int,
    base_url: str,
    window_started_at: datetime,
    window_finished_at: datetime,
    limit: int,
) -> str:
    raw = json.dumps(
        {
            "collector": "loki.query_range.v1",
            "connector_id": connector_id,
            "connector_revision": connector_revision,
            "base_url": base_url,
            "query": item.query,
            "start": window_started_at.isoformat(),
            "end": window_finished_at.isoformat(),
            "direction": "forward",
            "limit": limit,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def _run_wave(
    session,
    *,
    investigation: Investigation,
    step: InvestigationStep,
    connector: ApplicationIntegration,
    queries: list[LokiQuery],
    archived: dict[str, int],
    allowed_services: frozenset[str],
) -> tuple[list[int], list[dict[str, Any]]]:
    base_url = str((connector.config or {}).get("base_url") or "").rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError("Loki base_url must use HTTPS")
    limit = (connector.config or {}).get("limit", 1000)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5000:
        raise ValueError("Loki limit must be between 1 and 5000")
    fingerprints = {
        item: _fingerprint(
            item,
            connector_id=connector.id,
            connector_revision=connector.revision,
            base_url=base_url,
            window_started_at=investigation.window_started_at,
            window_finished_at=investigation.window_finished_at,
            limit=limit,
        )
        for item in queries
    }
    artifact_ids = [archived[fingerprints[item]] for item in queries if fingerprints[item] in archived]
    pending = [item for item in queries if fingerprints[item] not in archived]
    if not pending:
        return artifact_ids, []
    if len(pending) > MAX_WAVE_CONCURRENCY:
        raise ValueError("a Loki wave cannot exceed four operations")

    collections: list[EvidenceCollection] = []
    for item in pending:
        row = EvidenceCollection(
            investigation_id=investigation.id,
            step_id=step.id,
            connector_kind="loki",
            connector_id=connector.id,
            status="running",
            selector={"service_name": item.service_name, "phase": item.phase},
            config_hash=fingerprints[item],
            started_at=datetime.now(UTC),
        )
        session.add(row)
        collections.append(row)
    await session.flush()
    operations = await start_operations(
        session,
        [
            {
                "investigation_id": investigation.id,
                "step_id": step.id,
                "kind": "connector.loki.query",
                "actor": "collector",
                "title": f"查询 {item.service_name} 日志",
                "purpose": "在调查服务快照内按请求或业务关联键查询 Loki",
                "input_summary": {
                    "service_id": item.service_id,
                    "service_name": item.service_name,
                    "phase": item.phase,
                    "query_fingerprint": fingerprints[item],
                },
                "message": f"正在查询 {item.service_name} 的 {item.phase} 日志",
            }
            for item in pending
        ],
        commit=True,
    )
    headers = {}
    token = resolve_integration_secrets(connector.secrets_ciphertext).get("bearer_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async def execute(item: LokiQuery) -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {
                "query": item.query,
                "start": investigation.window_started_at.isoformat(),
                "end": investigation.window_finished_at.isoformat(),
                "direction": "forward",
                "limit": limit,
            }
        )
        return await asyncio.to_thread(
            _http_json,
            f"{base_url}/loki/api/v1/query_range?{params}",
            headers,
            15,
        )

    results = await asyncio.gather(*(execute(item) for item in pending), return_exceptions=True)
    payloads: list[dict[str, Any]] = []
    for item, collection, operation, result in zip(
        pending, collections, operations, results, strict=True
    ):
        if isinstance(result, BaseException):
            collection.status = "failed"
            collection.failure_code = type(result).__name__
            collection.failure_detail = str(result)[:1000]
            collection.finished_at = datetime.now(UTC)
            await finish_operation(
                session,
                operation,
                status="failed",
                result_summary="Loki 查询失败",
                message=f"{item.service_name} 日志查询失败",
                failure=str(result),
                commit=True,
            )
            continue
        payloads.append(result)
        result_peers, result_correlation, result_commits = _discovered(
            [result], default_service=item.service_name
        )
        raw = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
        excerpt, categories = mask_secrets(raw)
        artifact = EvidenceArtifact(
            investigation_id=investigation.id,
            collection_id=collection.id,
            artifact_type="log",
            source_kind="loki",
            source_id=connector.id,
            locator=base_url,
            content_hash=hashlib.sha256(raw.encode()).hexdigest(),
            redacted_excerpt=excerpt[:80_000],
            metadata_={
                "service_id": item.service_id,
                "service_name": item.service_name,
                "phase": item.phase,
                "query_fingerprint": fingerprints[item],
                "correlation_key": item.correlation_key,
                "correlation_value": item.correlation_value,
                "git_commits": sorted(result_commits.get(item.service_name, set())),
                "discovered_peer_services": sorted(result_peers),
                "unbound_peer_services": sorted(result_peers - allowed_services),
                "discovered_correlation": result_correlation,
                "secret_categories": categories,
            },
        )
        session.add(artifact)
        await session.flush()
        artifact_ids.append(artifact.id)
        archived[fingerprints[item]] = artifact.id
        collection.status = "succeeded"
        collection.artifact_count = 1
        collection.finished_at = datetime.now(UTC)
        await finish_operation(
            session,
            operation,
            status="succeeded",
            result_summary=f"已归档 {item.service_name} 的 {item.phase} 日志",
            message="Loki 证据已归档",
            metrics={"artifact_count": 1, "query_fingerprint": fingerprints[item]},
            evidence_refs=[artifact.id],
            commit=True,
        )
    return artifact_ids, payloads


async def collect_loki_evidence(
    session,
    *,
    investigation: Investigation,
    step: InvestigationStep,
    connector: ApplicationIntegration,
) -> list[int]:
    if connector.state != "active":
        return []
    snapshots = (
        await session.execute(
            select(InvestigationServiceSnapshot)
            .where(InvestigationServiceSnapshot.investigation_id == investigation.id)
            .order_by(InvestigationServiceSnapshot.role, InvestigationServiceSnapshot.service_name)
        )
    ).scalars().all()
    by_name = {item.service_name: item for item in snapshots}
    source = by_name.get(investigation.service_name or "")
    if source is None:
        raise ValueError("source service is outside the investigation snapshot")
    existing = (
        await session.execute(
            select(EvidenceArtifact).where(
                EvidenceArtifact.investigation_id == investigation.id,
                EvidenceArtifact.source_kind == "loki",
            )
        )
    ).scalars().all()
    archived = {
        str(item.metadata_["query_fingerprint"]): item.id
        for item in existing
        if (item.metadata_ or {}).get("query_fingerprint")
    }
    allowed_services = frozenset(by_name)
    peers: set[str] = set()
    correlation: dict[str, str] = {}
    for artifact in existing:
        metadata = artifact.metadata_ or {}
        peers.update(
            value
            for value in metadata.get("discovered_peer_services") or []
            if isinstance(value, str)
        )
        for key, value in (metadata.get("discovered_correlation") or {}).items():
            if key in CORRELATION_KEYS and isinstance(value, str) and value:
                correlation.setdefault(key, value)

    first_targets = snapshots if len(snapshots) <= MAX_WAVE_CONCURRENCY else [source]
    first_queries = [
        LokiQuery(
            service_id=item.service_id,
            service_name=item.service_name,
            query=build_request_logql(
                service_name=item.service_name,
                environment=investigation.environment or "",
                request_id=investigation.request_id or "",
            ),
            phase="request",
        )
        for item in first_targets
    ]
    artifact_ids, payloads = await _run_wave(
        session,
        investigation=investigation,
        step=step,
        connector=connector,
        queries=first_queries,
        archived=archived,
        allowed_services=allowed_services,
    )
    new_peers, new_correlation, _commits = _discovered(payloads)
    peers.update(new_peers)
    for key, value in new_correlation.items():
        correlation.setdefault(key, value)

    if len(snapshots) > MAX_WAVE_CONCURRENCY:
        queried = {source.service_name}
        frontier = sorted((peers & allowed_services) - queried)
        while frontier:
            target_names = frontier[:MAX_WAVE_CONCURRENCY]
            targets = [by_name[name] for name in target_names]
            queries = [
                LokiQuery(
                    service_id=item.service_id,
                    service_name=item.service_name,
                    query=build_request_logql(
                        service_name=item.service_name,
                        environment=investigation.environment or "",
                        request_id=investigation.request_id or "",
                    ),
                    phase="request",
                )
                for item in targets
            ]
            refs, new_payloads = await _run_wave(
                session,
                investigation=investigation,
                step=step,
                connector=connector,
                queries=queries,
                archived=archived,
                allowed_services=allowed_services,
            )
            artifact_ids.extend(refs)
            payloads.extend(new_payloads)
            queried.update(target_names)
            found_peers, newly_found, _ = _discovered(new_payloads)
            peers.update(found_peers)
            for key, value in newly_found.items():
                correlation.setdefault(key, value)
            frontier = sorted((peers & allowed_services) - queried)

    for key in CORRELATION_KEYS:
        if key not in correlation:
            continue
        for start in range(0, len(snapshots), MAX_WAVE_CONCURRENCY):
            queries = [
                LokiQuery(
                    service_id=item.service_id,
                    service_name=item.service_name,
                    query=build_lifecycle_logql(
                        service_name=item.service_name,
                        environment=investigation.environment or "",
                        correlation_key=key,
                        correlation_value=correlation[key],
                    ),
                    phase="lifecycle",
                    correlation_key=key,
                    correlation_value=correlation[key],
                )
                for item in snapshots[start : start + MAX_WAVE_CONCURRENCY]
            ]
            refs, _ = await _run_wave(
                session,
                investigation=investigation,
                step=step,
                connector=connector,
                queries=queries,
                archived=archived,
                allowed_services=allowed_services,
            )
            artifact_ids.extend(refs)
    return list(dict.fromkeys(artifact_ids))
