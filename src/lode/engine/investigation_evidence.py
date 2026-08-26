"""Read-only evidence collectors with bounded external-I/O waves."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from lode.db.models.git import GitRepo
from lode.db.models.integration import ApplicationIntegration
from lode.db.models.investigation import (
    EvidenceArtifact,
    EvidenceCollection,
    Investigation,
    InvestigationInput,
    InvestigationServiceSnapshot,
    InvestigationStep,
    SourceRevision,
)
from lode.engine.evidence.git import derive_query_terms, related_symbol_hits, search_tree, stack_hits
from lode.engine.evidence.secret_mask import mask_secrets
from lode.engine.db_proxy import execute_approved_query
from lode.engine.integrations import connector_for, resolve_integration_secrets
from lode.engine.investigation_events import finish_operation, progress_operation, start_operation, start_operations
from lode.engine.log_integrations import collect_log_evidence
from lode.integration_policy import integration_kind


def _hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _excerpt(value: object, limit: int = 40_000) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return mask_secrets(payload)[0][:limit]


def _language(path: str) -> str:
    return {
        ".cs": "csharp",
        ".go": "go",
        ".java": "java",
        ".js": "javascript",
        ".jsx": "javascript",
        ".kt": "kotlin",
        ".py": "python",
        ".rb": "ruby",
        ".rs": "rust",
        ".sql": "sql",
        ".ts": "typescript",
        ".tsx": "typescript",
    }.get(Path(path).suffix.lower(), "plaintext")


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


def _clone_exact_revision(
    repo: GitRepo,
    requested_ref: str,
    root: Path,
) -> tuple[Path, str]:
    """Resolve only the runtime-observed immutable revision."""
    root.mkdir(parents=True, exist_ok=True)
    checkout = root / f"repo-{repo.id}"
    _git(
        ["clone", "--filter=blob:none", "--no-checkout", repo.repo_url, str(checkout)],
        timeout=settings.evidence_git_clone_timeout_seconds,
    )
    _git(
        ["fetch", "--depth", "1", "origin", requested_ref],
        timeout=settings.evidence_git_clone_timeout_seconds,
        cwd=checkout,
    )
    _git(["checkout", "--force", "FETCH_HEAD"], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)
    sha = _git(["rev-parse", "HEAD"], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Git did not resolve an immutable 40-character revision")
    return checkout, sha


async def _collection(
    session,
    *,
    investigation_id: int,
    step_id: int,
    kind: str,
    connector_id: int | None,
    selector: dict[str, Any],
    config: dict[str, Any],
) -> EvidenceCollection:
    row = EvidenceCollection(
        investigation_id=investigation_id,
        step_id=step_id,
        connector_kind=kind,
        connector_id=connector_id,
        status="running",
        selector=selector,
        config_hash=_hash(config),
        started_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


def _context_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for raw in settings.evidence_git_context_paths.split(","):
        pattern = raw.strip()
        if not pattern:
            continue
        for path in root.glob(pattern):
            if path.is_file() and path.resolve().is_relative_to(root.resolve()) and path not in paths:
                paths.append(path)
                if len(paths) >= settings.evidence_git_context_max_files:
                    return paths
    return paths


async def collect_source_evidence(
    session,
    *,
    investigation: Investigation,
    incident_input: InvestigationInput,
    step: InvestigationStep,
) -> list[int]:
    """Collect exact stack locations first, then bounded lexical candidates."""
    service_repos = (
        await session.execute(
            select(InvestigationServiceSnapshot, GitRepo)
            .join(GitRepo, GitRepo.id == InvestigationServiceSnapshot.repo_id)
            .where(InvestigationServiceSnapshot.investigation_id == investigation.id)
            .order_by(InvestigationServiceSnapshot.role, InvestigationServiceSnapshot.service_name)
        )
    ).all()
    log_artifacts = (
        await session.execute(
            select(EvidenceArtifact).where(
                EvidenceArtifact.investigation_id == investigation.id,
                EvidenceArtifact.source_kind == "loki",
            )
        )
    ).scalars().all()
    commits: dict[str, set[str]] = {}
    if investigation.service_name and investigation.deployment_sha:
        commits.setdefault(investigation.service_name, set()).add(investigation.deployment_sha)
    for artifact in log_artifacts:
        metadata = artifact.metadata_ or {}
        service_name = metadata.get("service_name")
        for commit in metadata.get("git_commits") or []:
            if isinstance(service_name, str) and re.fullmatch(r"[0-9a-f]{40}", str(commit)):
                commits.setdefault(service_name, set()).add(str(commit))
    repo_targets = [
        (snapshot, repo, commit)
        for snapshot, repo in service_repos
        for commit in sorted(commits.get(snapshot.service_name, set()))
    ]
    discovery = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="repository.discovery",
        actor="engine",
        title="发现源码仓库",
        purpose="枚举调查服务快照映射的只读仓库与运行时版本",
        input_summary={"application_id": investigation.application_id},
        message="正在读取调查服务快照的仓库映射",
        commit=True,
    )
    await finish_operation(
        session,
        discovery,
        status="succeeded" if repo_targets else "blocked",
        result_summary=f"发现 {len(repo_targets)} 个事故服务版本" if repo_targets else "日志中没有可解析的事故 commit",
        message="事故源码版本发现完成" if repo_targets else "缺少事故 commit，禁止检出默认分支",
        metrics={"service_revision_count": len(repo_targets), "bound_service_count": len(service_repos)},
        commit=True,
    )
    if not repo_targets:
        return []

    # The incident baseline is always the immutable runtime-observed revision.
    requested_role = "incident"
    terms = derive_query_terms(incident_input)
    contract_terms = {
        term
        for term in terms
        if re.fullmatch(r"[A-Z][A-Z0-9_]{3,}", term)
        or term
        in {
            str(value)
            for key, value in (incident_input.fields or {}).items()
            if "code" in str(key).lower() and isinstance(value, str)
        }
    }
    incident_artifact = (
        await session.execute(
            select(EvidenceArtifact)
            .where(
                EvidenceArtifact.investigation_id == investigation.id,
                EvidenceArtifact.artifact_type == "incident_input",
            )
            .order_by(EvidenceArtifact.id)
        )
    ).scalars().first()
    artifact_ids: list[int] = []
    cache_root = Path(settings.evidence_git_cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix=f"investigation-{investigation.id}-", dir=cache_root))
    try:
        archived_rows = (
            await session.execute(
                select(EvidenceArtifact).where(
                    EvidenceArtifact.investigation_id == investigation.id,
                    EvidenceArtifact.source_kind.in_(["git", "git_context"]),
                )
            )
        ).scalars().all()
        archived_keys = {
            (int(metadata["service_id"]), str(metadata["revision"]))
            for artifact in archived_rows
            if (metadata := artifact.metadata_ or {}).get("service_id") is not None
            and metadata.get("revision")
        }
        resolved_revisions = (
            await session.execute(
                select(SourceRevision).where(
                    SourceRevision.investigation_id == investigation.id,
                    SourceRevision.status == "resolved",
                )
            )
        ).scalars().all()
        completed_keys = archived_keys & {
            (revision.service_id, str(revision.requested_ref))
            for revision in resolved_revisions
            if revision.service_id is not None and revision.requested_ref
        }
        checkout_results: dict[tuple[int, str], tuple[Path, str] | BaseException] = {}
        pending_targets = [
            target
            for target in repo_targets
            if (target[0].service_id, target[2]) not in completed_keys
        ]
        for wave_start in range(0, len(pending_targets), 4):
            wave = pending_targets[wave_start : wave_start + 4]
            operations = await start_operations(
                session,
                [
                    {
                        "investigation_id": investigation.id,
                        "step_id": step.id,
                        "kind": "git.checkout",
                        "actor": "collector",
                        "title": f"检出 {repo.name}",
                        "purpose": "解析并固定运行时日志记录的不可变源码版本",
                        "input_summary": {
                            "repo_id": repo.id,
                            "requested_ref": requested_ref,
                            "service_id": snapshot.service_id,
                            "service_name": snapshot.service_name,
                        },
                        "message": f"正在解析 {repo.name} 的源码版本",
                    }
                    for snapshot, repo, requested_ref in wave
                ],
                commit=True,
            )
            results = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        _clone_exact_revision,
                        repo,
                        requested_ref,
                        sandbox / f"{snapshot.service_id}-{requested_ref}",
                    )
                    for snapshot, repo, requested_ref in wave
                ),
                return_exceptions=True,
            )
            for (snapshot, _repo, requested_ref), operation, result in zip(
                wave, operations, results, strict=True
            ):
                key = (snapshot.service_id, requested_ref)
                checkout_results[key] = result
                revision = (
                    await session.execute(
                        select(SourceRevision).where(
                            SourceRevision.investigation_id == investigation.id,
                            SourceRevision.service_id == snapshot.service_id,
                            SourceRevision.requested_ref == requested_ref,
                        )
                    )
                ).scalars().first()
                if revision is None:
                    revision = SourceRevision(
                        investigation_id=investigation.id,
                        repo_id=_repo.id,
                        service_id=snapshot.service_id,
                        role=requested_role,
                        requested_ref=requested_ref,
                        resolution_basis="runtime_git_commit",
                        origin_url=_repo.repo_url,
                        status="queued",
                    )
                    session.add(revision)
                    await session.flush()
                revision.failure_detail = None
                if isinstance(result, BaseException):
                    revision.status = "failed"
                    revision.failure_detail = str(result)[:1_000]
                    await finish_operation(
                        session,
                        operation,
                        status="failed",
                        result_summary="无法解析请求的源码版本",
                        message="源码版本解析失败",
                        failure=result,
                        commit=True,
                    )
                    continue
                _checkout, sha = result
                revision.resolved_sha = sha
                revision.status = "resolved"
                revision.resolution_basis = "runtime_git_commit"
                await finish_operation(
                    session,
                    operation,
                    status="succeeded",
                    result_summary=f"已固定源码版本 {sha}",
                    message="不可变源码版本已就绪",
                    metrics={
                        "requested_ref": requested_ref,
                        "resolved_ref": requested_ref,
                        "resolved_sha": sha,
                        "revision_role": requested_role,
                        "resolution_basis": revision.resolution_basis,
                        "service_name": snapshot.service_name,
                    },
                    commit=True,
                )

        for snapshot, repo, requested_ref in repo_targets:
            revision = (
                await session.execute(
                    select(SourceRevision).where(
                        SourceRevision.investigation_id == investigation.id,
                        SourceRevision.service_id == snapshot.service_id,
                        SourceRevision.requested_ref == requested_ref,
                    )
                )
            ).scalars().first()
            archived = (
                await session.execute(
                    select(EvidenceArtifact.id).where(
                        EvidenceArtifact.investigation_id == investigation.id,
                        EvidenceArtifact.source_id == repo.id,
                        EvidenceArtifact.source_kind.in_(["git", "git_context"]),
                        EvidenceArtifact.metadata_["revision"].astext == requested_ref,
                        EvidenceArtifact.metadata_["service_id"].astext == str(snapshot.service_id),
                    )
                )
            ).scalars().all()
            if revision and revision.status == "resolved" and archived:
                artifact_ids.extend(int(item) for item in archived)
                continue
            if revision is None:
                raise RuntimeError("source revision was not persisted after checkout")
            collection = await _collection(
                session,
                investigation_id=investigation.id,
                step_id=step.id,
                kind="git",
                connector_id=repo.id,
                selector={
                    "repo_id": repo.id,
                    "revision_role": requested_role,
                    "requested_ref": requested_ref,
                    "service_id": snapshot.service_id,
                    "service_name": snapshot.service_name,
                },
                config={"repo_url": repo.repo_url, "default_branch": repo.default_branch},
            )
            key = (snapshot.service_id, requested_ref)
            result = checkout_results[key]
            if isinstance(result, BaseException):
                collection.status = "failed"
                collection.failure_code = type(result).__name__
                collection.failure_detail = str(result)[:1_000]
                collection.finished_at = datetime.now(UTC)
                await session.commit()
                continue
            checkout, sha = result

            search_operation = await start_operation(
                session,
                investigation_id=investigation.id,
                step_id=step.id,
                kind="source.stack_search",
                actor="collector",
                title="从报错堆栈定位代码",
                purpose="优先打开事故堆栈中的文件、行号与所属函数；未命中时再搜索错误标识",
                input_summary={"stack_present": bool(incident_input.error_stack), "term_count": len(terms), "revision": sha},
                message="正在按堆栈帧定位事故代码",
                commit=True,
            )
            try:
                exact_hits = await asyncio.to_thread(
                    stack_hits,
                    checkout,
                    incident_input.error_stack,
                    max_files=settings.evidence_git_max_files,
                    max_bytes=settings.evidence_git_max_bytes,
                )
                await progress_operation(
                    session,
                    search_operation,
                    message=f"堆栈精确命中 {len(exact_hits)} 个代码位置",
                    detail={"exact_stack_hits": len(exact_hits), "fallback_search": not bool(exact_hits)},
                    commit=True,
                )
                remaining = max(0, settings.evidence_git_max_files - len(exact_hits))
                lexical_hits = []
                if remaining:
                    lexical_hits = await asyncio.to_thread(
                        search_tree,
                        checkout,
                        terms,
                        max_files=max(1, (remaining * 2) // 3),
                        max_bytes=settings.evidence_git_max_bytes,
                        snippet_lines=max(48, settings.evidence_git_snippet_lines),
                    )
                related_budget = max(0, settings.evidence_git_max_files - len(exact_hits) - len(lexical_hits))
                relationship_hits = await asyncio.to_thread(
                    related_symbol_hits,
                    checkout,
                    [*exact_hits, *lexical_hits],
                    max_files=related_budget,
                    max_bytes=settings.evidence_git_max_bytes,
                ) if related_budget else []
            except Exception as exc:
                collection.status = "failed"
                collection.failure_code = type(exc).__name__
                collection.failure_detail = str(exc)[:1_000]
                collection.finished_at = datetime.now(UTC)
                await finish_operation(
                    session,
                    search_operation,
                    status="failed",
                    result_summary="源码搜索失败",
                    message=f"{snapshot.service_name} 源码搜索失败",
                    failure=exc,
                    commit=True,
                )
                continue
            primary_locations = {(item["path"], item["line"]) for item in exact_hits}
            hits = exact_hits + [hit for hit in [*lexical_hits, *relationship_hits] if (hit["path"], hit["line"]) not in primary_locations]
            repo_artifacts: list[int] = []
            for hit in hits[: settings.evidence_git_max_files]:
                masked, categories = mask_secrets(hit["snippet"])
                matched_contract_terms = sorted(set(hit.get("terms", [])) & contract_terms)
                selection_basis = (
                    "stack_frame"
                    if hit in exact_hits
                    else "alert_contract_candidate"
                    if matched_contract_terms
                    else "symbol_relationship_candidate"
                    if hit in relationship_hits
                    else "lexical_candidate"
                )
                artifact = EvidenceArtifact(
                    investigation_id=investigation.id,
                    collection_id=collection.id,
                    artifact_type="source_file",
                    source_kind="git",
                    source_id=repo.id,
                    locator=f"{repo.repo_url}@{sha}:{hit['path']}:{hit['snippet_start_line']}",
                    content_hash=_hash(hit["snippet"]),
                    redacted_excerpt=masked,
                    metadata_={
                        "revision_role": requested_role,
                        "revision": sha,
                        "resolution_basis": revision.resolution_basis,
                        "repo_id": repo.id,
                        "service_id": snapshot.service_id,
                        "service_name": snapshot.service_name,
                        "path": hit["path"],
                        "symbol": hit.get("symbol"),
                        "highlight_line": hit["line"],
                        "start_line": hit["snippet_start_line"],
                        "end_line": hit["snippet_end_line"],
                        "language": _language(hit["path"]),
                        "selection_basis": selection_basis,
                        "incident_link": hit.get("stack_frame") or (
                            f"incident_input:{incident_artifact.id}" if matched_contract_terms and incident_artifact else None
                        ),
                        "incident_evidence_id": incident_artifact.id if matched_contract_terms and incident_artifact else None,
                        "incident_contract_terms": matched_contract_terms,
                        "matched_terms": hit.get("terms", []),
                        "secret_categories": categories,
                    },
                )
                session.add(artifact)
                await session.flush()
                artifact_ids.append(artifact.id)
                repo_artifacts.append(artifact.id)

            context_count = 0
            remaining_bytes = settings.evidence_git_context_max_bytes
            for path in _context_files(checkout):
                raw = path.read_text(encoding="utf-8", errors="replace")[:remaining_bytes]
                if not raw:
                    continue
                remaining_bytes -= len(raw.encode())
                masked, categories = mask_secrets(raw)
                relative = str(path.relative_to(checkout))
                context = EvidenceArtifact(
                    investigation_id=investigation.id,
                    collection_id=collection.id,
                    artifact_type="source_file",
                    source_kind="git_context",
                    source_id=repo.id,
                    locator=f"{repo.repo_url}@{sha}:{relative}:1",
                    content_hash=_hash(raw),
                    redacted_excerpt=masked,
                    metadata_={"revision_role": requested_role, "revision": sha, "resolution_basis": revision.resolution_basis, "repo_id": repo.id, "service_id": snapshot.service_id, "service_name": snapshot.service_name, "path": relative, "start_line": 1, "end_line": max(1, len(raw.splitlines())), "language": _language(relative), "selection_basis": "repository_context", "secret_categories": categories},
                )
                session.add(context)
                await session.flush()
                artifact_ids.append(context.id)
                context_count += 1
                if remaining_bytes <= 0:
                    break
            collection.status = "succeeded"
            collection.artifact_count = len(repo_artifacts) + context_count
            collection.metadata_ = {"resolved_sha": sha, "resolved_ref": requested_ref, "resolution_basis": revision.resolution_basis, "revision_role": requested_role, "stack_hits": len(exact_hits), "lexical_candidates": len(lexical_hits), "related_symbols": len(relationship_hits)}
            collection.finished_at = datetime.now(UTC)
            await finish_operation(
                session,
                search_operation,
                status="succeeded" if repo_artifacts else "partial",
                result_summary=f"归档 {len(repo_artifacts)} 个代码范围，其中 {len(exact_hits)} 个来自堆栈精确位置",
                message="源码定位与归档完成",
                metrics={"stack_hits": len(exact_hits), "lexical_candidates": len(lexical_hits), "related_symbols": len(relationship_hits), "context_files": context_count},
                evidence_refs=repo_artifacts,
                commit=True,
            )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    return artifact_ids


def _http_json(url: str, *, headers: dict[str, str], timeout: int) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


async def collect_connector_evidence(
    session,
    *,
    investigation: Investigation,
    step: InvestigationStep,
    connector: ApplicationIntegration,
) -> list[int]:
    """Execute one server-registered connector capability and await it fully."""
    if "log_search" in integration_kind(connector.kind).capabilities:
        return await collect_log_evidence(
            session, investigation=investigation, step=step, integration=connector
        )
    archived = list(
        (
            await session.execute(
                select(EvidenceArtifact.id).where(
                    EvidenceArtifact.investigation_id == investigation.id,
                    EvidenceArtifact.source_id == connector.id,
                    EvidenceArtifact.source_kind == connector.kind,
                )
            )
        ).scalars()
    )
    if archived:
        operation = await start_operation(
            session,
            investigation_id=investigation.id,
            step_id=step.id,
            kind=f"connector.{connector.kind}.resume",
            actor="engine",
            title=f"恢复 {connector.name} 采集",
            purpose="复用中断前已完整归档的连接器证据",
            input_summary={"connector_id": connector.id},
            message="正在校验已归档连接器证据",
            commit=True,
        )
        await finish_operation(
            session,
            operation,
            status="succeeded",
            result_summary=f"复用 {len(archived)} 项已归档证据",
            message="无需重复调用连接器",
            metrics={"reused_artifact_count": len(archived)},
            evidence_refs=[int(item) for item in archived],
            commit=True,
        )
        return [int(item) for item in archived]
    config = dict(connector.config or {})
    selector = dict(config.get("selector") or {})
    collection = await _collection(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind=connector.kind,
        connector_id=connector.id,
        selector=selector,
        config=config,
    )
    operation = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind=f"connector.{connector.kind}",
        actor="collector",
        title=f"采集 {connector.name}",
        purpose="执行管理员预先注册的只读证据能力",
        input_summary={"connector_id": connector.id, "kind": connector.kind, "selector": selector, "window": [investigation.window_started_at, investigation.window_finished_at]},
        message=f"正在采集 {connector.name}",
        commit=True,
    )
    if connector.state != "active":
        collection.status = "blocked"
        collection.failure_detail = "connector disabled"
        collection.finished_at = datetime.now(UTC)
        await finish_operation(session, operation, status="blocked", result_summary="连接器已停用", message="采集被阻止", failure="connector disabled", commit=True)
        return []
    try:
        if connector.kind == "prometheus":
            base_url = str(config.get("base_url") or "").rstrip("/")
            secrets = resolve_integration_secrets(connector.secrets_ciphertext)
            headers = {"Authorization": f"Bearer {secrets['bearer_token']}"} if secrets.get("bearer_token") else {}
            query = str(config.get("query") or selector.get("promql") or "")
            if not base_url or not query:
                raise ValueError("administrator-approved promql selector is required")
            params = {"query": query, "start": investigation.window_started_at.isoformat(), "end": investigation.window_finished_at.isoformat()}
            url = f"{base_url}/api/v1/query_range?{urllib.parse.urlencode(params)}"
            artifact_type = "metric"
            payload = await asyncio.to_thread(_http_json, url, headers=headers, timeout=15)
            locator = base_url
            summary = f"{connector.kind} incident-window snapshot"
        else:
            raise ValueError(f"unsupported observability connector kind: {connector.kind}")
        artifact = EvidenceArtifact(
            investigation_id=investigation.id,
            collection_id=collection.id,
            artifact_type=artifact_type,
            source_kind=connector.kind,
            source_id=connector.id,
            locator=locator,
            content_hash=_hash(payload),
            redacted_excerpt=_excerpt(payload),
            metadata_={"summary": summary, "window_started_at": investigation.window_started_at.isoformat(), "window_finished_at": investigation.window_finished_at.isoformat(), "selector": selector},
        )
        session.add(artifact)
        await session.flush()
        collection.status = "succeeded"
        collection.artifact_count = 1
        collection.finished_at = datetime.now(UTC)
        await finish_operation(session, operation, status="succeeded", result_summary=summary, message="连接器证据已归档", metrics={"artifact_count": 1}, evidence_refs=[artifact.id], commit=True)
        return [artifact.id]
    except ValueError as exc:
        status = "blocked"
        collection.status = status
        collection.failure_code = type(exc).__name__
        collection.failure_detail = str(exc)[:1_000]
        collection.finished_at = datetime.now(UTC)
        await finish_operation(session, operation, status=status, result_summary="连接器缺少受控配置", message="采集无法执行", failure=exc, commit=True)
        return []
    except Exception as exc:
        collection.status = "failed"
        collection.failure_code = type(exc).__name__
        collection.failure_detail = str(exc)[:1_000]
        collection.finished_at = datetime.now(UTC)
        await finish_operation(session, operation, status="failed", result_summary="连接器采集失败", message="采集失败", failure=exc, commit=True)
        return []


async def collect_integration_evidence(
    session,
    *,
    investigation: Investigation,
    step: InvestigationStep,
    integration: ApplicationIntegration,
) -> list[int]:
    """Collect a fixed read-only snapshot from an existing application integration."""
    if integration.application_id != investigation.application_id:
        raise ValueError("integration is outside the investigation application boundary")
    current = await session.get(ApplicationIntegration, integration.id)
    if current is None or current.revision != integration.revision:
        raise ValueError("integration configuration changed during investigation")
    capabilities = integration_kind(integration.kind).capabilities
    if "log_search" in capabilities:
        return await collect_log_evidence(
            session, investigation=investigation, step=step, integration=integration
        )
    archived = list(
        (
            await session.execute(
                select(EvidenceArtifact.id).where(
                    EvidenceArtifact.investigation_id == investigation.id,
                    EvidenceArtifact.source_kind == integration.kind,
                    EvidenceArtifact.source_id == integration.id,
                )
            )
        ).scalars()
    )
    if archived:
        return [int(item) for item in archived]

    config = dict(integration.config or {})
    collection = await _collection(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind=integration.kind,
        connector_id=integration.id,
        selector={},
        config=config,
    )
    operation = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind=f"integration.{integration.kind}",
        actor="collector",
        title=f"采集 {integration.name}",
        purpose="执行现有应用集成提供的固定只读状态快照",
        input_summary={"integration_id": integration.id, "kind": integration.kind},
        message=f"正在采集 {integration.name}",
        commit=True,
    )
    try:
        if integration.state != "active":
            raise ValueError("integration disabled")
        if "snapshot" not in capabilities:
            raise ValueError("integration does not expose a snapshot capability")
        client = connector_for(integration.kind)
        credential = resolve_integration_secrets(integration.secrets_ciphertext)
        await asyncio.wait_for(
            client.verify_readonly(config, credential),
            timeout=15,
        )
        snapshot = await asyncio.wait_for(
            client.collect_snapshot(config, credential),
            timeout=15,
        )
        payload = snapshot.payload
        artifact = EvidenceArtifact(
            investigation_id=investigation.id,
            collection_id=collection.id,
            artifact_type="dependency",
            source_kind=integration.kind,
            source_id=integration.id,
            locator=snapshot.locator,
            content_hash=_hash(payload),
            redacted_excerpt=_excerpt(payload),
            metadata_={"summary": snapshot.summary, "integration_id": integration.id},
        )
        session.add(artifact)
        await session.flush()
        collection.status = "succeeded"
        collection.artifact_count = 1
        collection.finished_at = datetime.now(UTC)
        integration.last_collected_at = datetime.now(UTC)
        integration.last_error = None
        await finish_operation(
            session,
            operation,
            status="succeeded",
            result_summary=snapshot.summary,
            message="集成证据已归档",
            metrics={"artifact_count": 1},
            evidence_refs=[artifact.id],
            commit=True,
        )
        return [artifact.id]
    except ValueError as exc:
        collection.status = "blocked"
        collection.failure_code = type(exc).__name__
        collection.failure_detail = str(exc)[:1_000]
        collection.finished_at = datetime.now(UTC)
        integration.last_error = str(exc)[:1_000]
        await finish_operation(session, operation, status="blocked", result_summary="集成不可用", message="采集被阻止", failure=exc, commit=True)
        return []
    except Exception as exc:
        collection.status = "failed"
        collection.failure_code = type(exc).__name__
        collection.failure_detail = str(exc)[:1_000]
        collection.finished_at = datetime.now(UTC)
        integration.last_error = str(exc)[:1_000]
        await finish_operation(session, operation, status="failed", result_summary="集成采集失败", message="采集失败", failure=exc, commit=True)
        return []


async def collect_database_evidence(
    session,
    *,
    investigation: Investigation,
    step: InvestigationStep,
    integration: ApplicationIntegration,
    table: str,
) -> list[int]:
    """Execute the server-owned sample template for one approved table."""
    if integration.application_id != investigation.application_id:
        raise ValueError("database integration is outside the investigation application boundary")
    locator = f"database-integration:{integration.id}:sample:{table}:r{integration.revision}"
    archived = list(
        (
            await session.execute(
                select(EvidenceArtifact.id).where(
                    EvidenceArtifact.investigation_id == investigation.id,
                    EvidenceArtifact.source_kind == "database",
                    EvidenceArtifact.source_id == integration.id,
                    EvidenceArtifact.locator == locator,
                )
            )
        ).scalars()
    )
    if archived:
        return [int(item) for item in archived]

    selector = {"table": table, "operation": "sample"}
    collection = await _collection(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="database",
        connector_id=integration.id,
        selector=selector,
        config={"integration_id": integration.id, "revision": integration.revision},
    )
    operation = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="database.sample",
        actor="collector",
        title=f"读取 {integration.name}.{table}",
        purpose="执行服务器预定义且经管理员表白名单授权的只读样本查询",
        input_summary={"integration_id": integration.id, "revision": integration.revision, **selector},
        message=f"正在读取 {table}",
        commit=True,
    )
    try:
        payload = await execute_approved_query(
            dict(integration.config or {}),
            resolve_integration_secrets(integration.secrets_ciphertext),
            table=table, operation="sample", timeout=15,
        )
        artifact = EvidenceArtifact(
            investigation_id=investigation.id,
            collection_id=collection.id,
            artifact_type="database",
            source_kind="database",
            source_id=integration.id,
            locator=locator,
            content_hash=_hash(payload),
            redacted_excerpt=_excerpt(payload),
            metadata_={"summary": f"Database approved sample: {table}", "revision": integration.revision, **selector},
        )
        session.add(artifact)
        await session.flush()
        collection.status = "succeeded"
        collection.artifact_count = 1
        collection.finished_at = datetime.now(UTC)
        await finish_operation(session, operation, status="succeeded", result_summary=f"已归档 {table} 的脱敏样本", message="数据库证据已归档", metrics={"artifact_count": 1, "row_count": payload.get("row_count", 0)}, evidence_refs=[artifact.id], commit=True)
        return [artifact.id]
    except ValueError as exc:
        collection.status = "blocked"
        collection.failure_code = type(exc).__name__
        collection.failure_detail = str(exc)[:1_000]
        collection.finished_at = datetime.now(UTC)
        await finish_operation(session, operation, status="blocked", result_summary="数据库查询未获授权", message="采集被阻止", failure=exc, commit=True)
        return []
    except Exception as exc:
        collection.status = "failed"
        collection.failure_code = type(exc).__name__
        collection.failure_detail = str(exc)[:1_000]
        collection.finished_at = datetime.now(UTC)
        await finish_operation(session, operation, status="failed", result_summary="数据库采集失败", message="采集失败", failure=exc, commit=True)
        return []
