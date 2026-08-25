"""Bounded, read-only evidence collectors for canonical investigations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from lode.config import settings
from lode.crypto import decrypt_secret
from lode.db.models.application import ApplicationRepo
from lode.db.models.git import GitRepo
from lode.db.models.investigation import EvidenceArtifact, EvidenceCollection, EvidenceConnector, Investigation, SourceRevision
from lode.engine.evidence.git import derive_query_terms, search_tree
from lode.engine.evidence.secret_mask import mask_secrets
from lode.engine.investigation_events import append_execution_event
from lode.engine.integrations import connector_for
from lode.integration_policy import normalize_integration_config

logger = logging.getLogger("lode.engine.investigation_evidence")


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _excerpt(value: object, limit: int = 20_000) -> str:
    return mask_secrets(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))[0][:limit]


def _secret(ref: str | None) -> str:
    if not ref:
        return ""
    if ref.startswith("env://"):
        import os
        return os.environ.get(ref[6:], "")
    return decrypt_secret(ref) or ""


async def _stage_collection(session, investigation_id: int, stage_id: int, kind: str, connector_id: int | None, selector: dict, config: dict | None = None) -> EvidenceCollection:
    row = EvidenceCollection(
        investigation_id=investigation_id,
        stage_id=stage_id,
        connector_kind=kind,
        connector_id=connector_id,
        status="running",
        selector=selector,
        config_hash=_hash(config or {}),
        started_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def _finish_collection(session, row: EvidenceCollection, status: str, *, artifacts: int = 0, error: Exception | str | None = None, metadata: dict | None = None) -> None:
    row.status = status
    row.artifact_count = artifacts
    row.finished_at = datetime.now(UTC)
    if error:
        row.failure_code = type(error).__name__ if isinstance(error, Exception) else "collector_error"
        row.failure_detail = str(error)[:1000]
    if metadata:
        row.metadata_ = metadata
    await session.flush()


def _git(command: list[str], *, timeout: int, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "protocol.ext.allow=never", *command],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def _resolve_remote_ref(repo: GitRepo, ref: str) -> str:
    output = _git(["ls-remote", repo.repo_url, ref], timeout=settings.evidence_git_clone_timeout_seconds)
    first = output.splitlines()[0] if output else ""
    return first.split()[0] if first else ""


def _clone(repo: GitRepo, root: Path) -> Path:
    checkout = root / f"repo-{repo.id}-{hashlib.sha1(repo.repo_url.encode()).hexdigest()[:10]}"
    _git(["clone", "--depth", "1", "--no-checkout", repo.repo_url, str(checkout)], timeout=settings.evidence_git_clone_timeout_seconds)
    return checkout


def _fetch(checkout: Path, ref: str, default_branch: str) -> None:
    try:
        _git(["fetch", "--depth", "1", "origin", ref], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)
    except subprocess.CalledProcessError:
        _git(["fetch", "--depth", "1", "origin", default_branch], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)


def _checkout(checkout: Path) -> str:
    _git(["checkout", "--force", "FETCH_HEAD"], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)
    return _git(["rev-parse", "HEAD"], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript", ".go": "go", ".java": "java", ".rs": "rust", ".sql": "sql", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".sh": "shell"}.get(suffix, "plaintext")


def _context_files(root: Path) -> list[Path]:
    """Return only administrator-approved, bounded repository context files."""
    paths: list[Path] = []
    for raw in settings.evidence_git_context_paths.split(","):
        pattern = raw.strip()
        if not pattern:
            continue
        for candidate in root.glob(pattern):
            if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()) and candidate not in paths:
                paths.append(candidate)
                if len(paths) >= settings.evidence_git_context_max_files:
                    return paths
    return paths


def _read_context(path: Path, remaining: int) -> str:
    return path.read_text(encoding="utf-8", errors="replace")[:remaining]


def _bounded_diff(incident: Path, latest: Path, *, max_bytes: int) -> str:
    """Return a non-executable, size-bounded source diff for two fixed trees."""
    result = subprocess.run(
        ["git", "-c", "core.pager=cat", "diff", "--no-ext-diff", "--no-index", "--unified=3", str(incident), str(latest)],
        check=False,
        capture_output=True,
        text=True,
        timeout=settings.evidence_git_clone_timeout_seconds,
    )
    return result.stdout[:max_bytes]


async def collect_source_evidence(session, *, investigation_id: int, stage_id: int, alert) -> list[SourceRevision]:
    """Resolve immutable incident/latest revisions and persist bounded snippets."""
    repos = (await session.execute(
        select(GitRepo)
        .join(ApplicationRepo, ApplicationRepo.repo_id == GitRepo.id)
        .join(Investigation, Investigation.application_id == ApplicationRepo.application_id)
        .where(Investigation.id == investigation_id)
    )).scalars().all()
    discovery = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, event_type="repository_discovery", phase="started", detail={"configured_repositories": len(repos)}, commit=True)
    if not repos:
        await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, event_type="repository_discovery", phase="not_configured", operation_id=discovery, detail={"reason": "no repository is attached to this application"}, commit=True)
        return []
    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, event_type="repository_discovery", phase="succeeded", operation_id=discovery, detail={"configured_repositories": len(repos)}, commit=True)
    fields = getattr(alert, "fields", {}) or {}
    incident_ref = next((str(fields[key]).strip() for key in ("commit", "git_commit", "sha", "revision", "ref") if fields.get(key)), None)
    terms_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, event_type="search_terms", phase="started", detail={}, commit=True)
    terms = derive_query_terms(alert)
    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, event_type="search_terms", phase="succeeded", operation_id=terms_operation, detail={"term_count": len(terms), "terms": terms}, commit=True)
    revisions: list[SourceRevision] = []
    sandbox_parent = Path(settings.evidence_git_cache_dir)
    sandbox_parent.mkdir(parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix=f"investigation-{investigation_id}-", dir=sandbox_parent))
    try:
        for repo in repos:
            requested = incident_ref or repo.default_branch
            targets = [("incident", requested), ("latest", repo.default_branch)]
            checkouts: dict[str, tuple[Path, str, EvidenceCollection]] = {}
            for role, ref in targets:
                revision = SourceRevision(investigation_id=investigation_id, repo_id=repo.id, role=role, requested_ref=ref, origin_url=repo.repo_url, status="queued")
                session.add(revision)
                await session.flush()
                collection = await _stage_collection(session, investigation_id, stage_id, "git", repo.id, {"role": role, "requested_ref": ref}, {"url": repo.repo_url, "branch": repo.default_branch})
                try:
                    (sandbox / role).mkdir(parents=True, exist_ok=True)
                    clone_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="git_clone", phase="started", detail={"repository": repo.name, "role": role, "requested_ref": ref}, commit=True)
                    checkout = await asyncio.to_thread(_clone, repo, sandbox / role)
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="git_clone", phase="succeeded", operation_id=clone_operation, detail={"repository": repo.name, "role": role}, commit=True)
                    fetch_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="git_fetch", phase="started", detail={"requested_ref": ref}, commit=True)
                    await asyncio.to_thread(_fetch, checkout, ref, repo.default_branch)
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="git_fetch", phase="succeeded", operation_id=fetch_operation, detail={"requested_ref": ref}, commit=True)
                    checkout_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="git_checkout", phase="started", detail={"requested_ref": ref}, commit=True)
                    sha = await asyncio.to_thread(_checkout, checkout)
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="git_checkout", phase="succeeded", operation_id=checkout_operation, detail={"resolved_sha": sha}, commit=True)
                    revision.resolved_sha = sha
                    revision.status = "resolved"
                    checkouts[role] = (checkout, sha, collection)
                    context_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="context_discovery", phase="started", detail={"allow_paths": settings.evidence_git_context_paths}, commit=True)
                    context_paths = _context_files(checkout)
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="context_discovery", phase="succeeded", operation_id=context_operation, detail={"files": [str(path.relative_to(checkout)) for path in context_paths]}, commit=True)
                    context_artifacts = 0
                    remaining_context_bytes = settings.evidence_git_context_max_bytes
                    for context_path in context_paths:
                        relative = str(context_path.relative_to(checkout))
                        read_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="context_read", phase="started", detail={"path": relative, "remaining_budget": remaining_context_bytes}, commit=True)
                        raw_context = await asyncio.to_thread(_read_context, context_path, remaining_context_bytes)
                        remaining_context_bytes -= len(raw_context.encode("utf-8"))
                        masked, categories = mask_secrets(raw_context)
                        artifact = EvidenceArtifact(investigation_id=investigation_id, collection_id=collection.id, artifact_type="source_file", source_kind="git", source_id=repo.id, locator=f"{repo.repo_url}@{sha}:{relative}:1", content_hash=_hash(raw_context), redacted_excerpt=masked, metadata_={"role": "repository_context", "sha": sha, "path": relative, "line": 1, "language": _language(relative), "secret_categories": categories, "time_scope": "source_revision"})
                        session.add(artifact)
                        await session.flush()
                        context_artifacts += 1
                        await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="context_read", phase="succeeded", operation_id=read_operation, detail={"path": relative, "bytes": len(raw_context.encode("utf-8")), "language": _language(relative)}, artifact_refs=[artifact.id], commit=True)
                        if remaining_context_bytes <= 0:
                            break
                    search_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="source_search", phase="started", detail={"term_count": len(terms)}, commit=True)
                    hits = search_tree(checkout, terms, max_files=settings.evidence_git_max_files, max_bytes=settings.evidence_git_max_bytes, snippet_lines=settings.evidence_git_snippet_lines)
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="source_search", phase="succeeded", operation_id=search_operation, detail={"matches": len(hits), "max_files": settings.evidence_git_max_files}, commit=True)
                    hit_artifact_ids: list[int] = []
                    for hit in hits:
                        masked, categories = mask_secrets(hit["snippet"])
                        locator = f"{repo.repo_url}@{sha}:{hit['path']}:{hit['line']}"
                        artifact = EvidenceArtifact(
                            investigation_id=investigation_id, collection_id=collection.id,
                            artifact_type="source_file", source_kind="git", source_id=repo.id,
                            locator=locator, content_hash=_hash(hit["snippet"]), redacted_excerpt=masked,
                            metadata_={"role": role, "sha": sha, "path": hit["path"], "terms": hit["terms"], "line": hit["line"], "language": _language(hit["path"]), "secret_categories": categories, "time_scope": "source_revision"},
                        )
                        session.add(artifact)
                        await session.flush()
                        hit_artifact_ids.append(artifact.id)
                    await session.flush()
                    archive_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="source_archive", phase="succeeded", detail={"source_matches": len(hits), "context_files": context_artifacts}, artifact_refs=hit_artifact_ids, commit=True)
                    await _finish_collection(session, collection, "succeeded", artifacts=len(hits) + context_artifacts, metadata={"resolved_sha": sha})
                    await session.commit()
                except Exception as exc:  # collector failure is explicit evidence, not a hidden fallback
                    revision.status = "failed"
                    revision.failure_detail = str(exc)[:1000]
                    await _finish_collection(session, collection, "failed", error=exc)
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=collection.id, event_type="source_collection", phase="failed", detail={"requested_ref": ref, "error": str(exc)}, commit=True)
                revisions.append(revision)
            if "incident" in checkouts and "latest" in checkouts:
                incident_checkout, incident_sha, _ = checkouts["incident"]
                latest_checkout, latest_sha, latest_collection = checkouts["latest"]
                try:
                    diff_operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=latest_collection.id, event_type="source_diff", phase="started", detail={"incident_sha": incident_sha, "latest_sha": latest_sha}, commit=True)
                    diff = await asyncio.to_thread(_bounded_diff, incident_checkout, latest_checkout, max_bytes=settings.evidence_git_max_bytes)
                    if diff:
                        masked, categories = mask_secrets(diff)
                        artifact = EvidenceArtifact(
                            investigation_id=investigation_id, collection_id=latest_collection.id,
                            artifact_type="source_diff", source_kind="git", source_id=repo.id,
                            locator=f"{repo.repo_url}@{incident_sha}..{latest_sha}", content_hash=_hash(diff), redacted_excerpt=masked,
                            metadata_={"incident_sha": incident_sha, "latest_sha": latest_sha, "bounded": len(diff) >= settings.evidence_git_max_bytes, "secret_categories": categories, "time_scope": "source_revision_diff"},
                        )
                        session.add(artifact)
                        latest_collection.artifact_count += 1
                        await session.flush()
                        await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=latest_collection.id, event_type="source_diff", phase="succeeded", operation_id=diff_operation, detail={"incident_sha": incident_sha, "latest_sha": latest_sha, "bounded": len(diff) >= settings.evidence_git_max_bytes}, artifact_refs=[artifact.id], commit=True)
                    else:
                        await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=latest_collection.id, event_type="source_diff", phase="succeeded", operation_id=diff_operation, detail={"incident_sha": incident_sha, "latest_sha": latest_sha, "empty": True}, commit=True)
                except Exception as exc:
                    # The two immutable revisions remain valid evidence even if
                    # their bounded comparison cannot be generated.
                    logger.warning("unable to build source diff for repo %s: %s", repo.id, exc)
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=latest_collection.id, event_type="source_diff", phase="failed", operation_id=diff_operation, detail={"error": str(exc)}, commit=True)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    return revisions


def _http_json(url: str, *, headers: dict[str, str], timeout: int) -> Any:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _time_params(window_started_at: datetime, window_finished_at: datetime) -> tuple[str, str]:
    return str(int(window_started_at.timestamp() * 1_000_000_000)), str(int(window_finished_at.timestamp() * 1_000_000_000))


async def collect_observability_evidence(session, *, investigation_id: int, stage_id: int, connectors: list[EvidenceConnector], window_started_at: datetime, window_finished_at: datetime, trace_id: str | None) -> list[EvidenceCollection]:
    """Collect Loki, Prometheus and Tempo through fixed admin-configured selectors."""
    start_ns, end_ns = _time_params(window_started_at, window_finished_at)

    async def collect(connector: EvidenceConnector) -> EvidenceCollection:
        config = dict(connector.config or {})
        selector = dict(config.get("selector") or {})
        row = await _stage_collection(session, investigation_id, stage_id, connector.kind, connector.id, selector, config)
        operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="started", detail={"connector": connector.name, "kind": connector.kind, "budget_seconds": connector.collection_budget_seconds, "selector": selector}, commit=True)
        if connector.state != "active":
            await _finish_collection(session, row, "blocked", error="connector disabled")
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="blocked", operation_id=operation, detail={"connector": connector.name, "reason": "connector disabled"}, commit=True)
            return row
        base_url = str(config.get("base_url") or "").rstrip("/")
        if not base_url:
            await _finish_collection(session, row, "not_configured", error="base_url is required")
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="not_configured", operation_id=operation, detail={"connector": connector.name, "reason": "base_url is required"}, commit=True)
            return row
        token = _secret(connector.secret_ref)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            if connector.kind == "loki":
                query = str(config.get("query") or selector.get("logql") or "")
                if not query:
                    raise ValueError("administrator-approved LogQL query is required")
                url = f"{base_url}/loki/api/v1/query_range?{urllib.parse.urlencode({'query': query, 'start': start_ns, 'end': end_ns, 'limit': min(int(config.get('limit', 200)), 500)})}"
                payload = await asyncio.to_thread(_http_json, url, headers=headers, timeout=connector.collection_budget_seconds)
                artifact_type = "log"
            elif connector.kind == "prometheus":
                query = str(config.get("query") or selector.get("promql") or "")
                if not query:
                    raise ValueError("administrator-approved PromQL query is required")
                url = f"{base_url}/api/v1/query_range?{urllib.parse.urlencode({'query': query, 'start': window_started_at.isoformat(), 'end': window_finished_at.isoformat(), 'step': str(config.get('step', '30s'))})}"
                payload = await asyncio.to_thread(_http_json, url, headers=headers, timeout=connector.collection_budget_seconds)
                artifact_type = "metric"
            else:
                if not trace_id:
                    await _finish_collection(session, row, "not_configured", error="alert has no trace_id")
                    await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="not_configured", operation_id=operation, detail={"connector": connector.name, "reason": "alert has no trace_id"}, commit=True)
                    return row
                url = f"{base_url}/api/traces/{urllib.parse.quote(trace_id, safe='')}"
                payload = await asyncio.to_thread(_http_json, url, headers=headers, timeout=connector.collection_budget_seconds)
                artifact_type = "trace"
            excerpt = _excerpt(payload)
            artifact = EvidenceArtifact(investigation_id=investigation_id, collection_id=row.id, artifact_type=artifact_type, source_kind=connector.kind, source_id=connector.id, locator=base_url, content_hash=_hash(payload), redacted_excerpt=excerpt, metadata_={"window_started_at": window_started_at.isoformat(), "window_finished_at": window_finished_at.isoformat(), "selector": selector, "trace_id": trace_id})
            session.add(artifact)
            await session.flush()
            await _finish_collection(session, row, "succeeded", artifacts=1)
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="succeeded", operation_id=operation, detail={"connector": connector.name, "artifact_type": artifact_type}, artifact_refs=[artifact.id], commit=True)
        except Exception as exc:
            await _finish_collection(session, row, "failed", error=exc)
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="failed", operation_id=operation, detail={"connector": connector.name, "error": str(exc)}, commit=True)
        return row

    # Each collection mutates the investigation's unit-of-work. Network I/O in
    # the collectors is offloaded, but SQLAlchemy sessions are intentionally not
    # shared across concurrent writers.
    return [await collect(connector) for connector in connectors]


async def collect_dependency_evidence(session, *, investigation_id: int, stage_id: int, connectors: list[EvidenceConnector]) -> list[EvidenceCollection]:
    """Run only typed connector probes; no LLM-authored command reaches a service."""
    async def collect(connector: EvidenceConnector) -> EvidenceCollection:
        config = dict(connector.config or {})
        row = await _stage_collection(session, investigation_id, stage_id, connector.kind, connector.id, dict(config.get("selector") or {}), config)
        operation = await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="started", detail={"connector": connector.name, "kind": connector.kind, "budget_seconds": connector.collection_budget_seconds}, commit=True)
        if connector.state != "active":
            await _finish_collection(session, row, "blocked", error="connector disabled")
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="blocked", operation_id=operation, detail={"connector": connector.name, "reason": "connector disabled"}, commit=True)
            return row
        if connector.kind == "postgres":
            profile = dict(connector.diagnostic_profile or {})
            if not profile.get("catalog"):
                await _finish_collection(session, row, "not_configured", error="approved PostgreSQL diagnostic profile is required")
                await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="not_configured", operation_id=operation, detail={"connector": connector.name, "reason": "approved diagnostic profile is required"}, commit=True)
                return row
            # PostgreSQL execution is intentionally delegated to the existing server-owned
            # query catalog in a subsequent profile executor; arbitrary SQL is never stored.
            await _finish_collection(session, row, "not_configured", error="no approved executable diagnostic template")
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="not_configured", operation_id=operation, detail={"connector": connector.name, "reason": "no approved executable diagnostic template"}, commit=True)
            return row
        try:
            normalized = normalize_integration_config(connector.kind, config)
            client = connector_for(connector.kind)
            credential = _secret(connector.secret_ref)
            await asyncio.wait_for(client.verify_readonly(normalized, credential), timeout=connector.collection_budget_seconds)
            snapshot = await asyncio.wait_for(client.collect_snapshot(normalized, credential), timeout=connector.collection_budget_seconds)
            payload = getattr(snapshot, "payload", {})
            artifact = EvidenceArtifact(investigation_id=investigation_id, collection_id=row.id, artifact_type="dependency", source_kind=connector.kind, source_id=connector.id, locator=getattr(snapshot, "locator", connector.name), content_hash=_hash(payload), redacted_excerpt=_excerpt(payload), metadata_={"summary": getattr(snapshot, "summary", ""), "collector_version": "1"})
            session.add(artifact)
            await session.flush()
            await _finish_collection(session, row, "succeeded", artifacts=1)
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="succeeded", operation_id=operation, detail={"connector": connector.name, "artifact_type": "dependency"}, artifact_refs=[artifact.id], commit=True)
        except ValueError as exc:
            await _finish_collection(session, row, "blocked", error=exc)
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="blocked", operation_id=operation, detail={"connector": connector.name, "error": str(exc)}, commit=True)
        except Exception as exc:
            await _finish_collection(session, row, "failed", error=exc)
            await append_execution_event(session, investigation_id=investigation_id, stage_id=stage_id, collection_id=row.id, event_type="connector_collection", phase="failed", operation_id=operation, detail={"connector": connector.name, "error": str(exc)}, commit=True)
        return row

    return [await collect(connector) for connector in connectors]
