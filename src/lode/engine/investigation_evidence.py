"""Sequential, read-only evidence collectors for V1 investigations."""

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
from lode.crypto import decrypt_secret
from lode.db.models.application import ApplicationRepo
from lode.db.models.git import GitRepo
from lode.db.models.investigation import (
    EvidenceArtifact,
    EvidenceCollection,
    EvidenceConnector,
    Investigation,
    InvestigationInput,
    InvestigationStep,
    SourceRevision,
)
from lode.engine.evidence.git import derive_query_terms, related_symbol_hits, search_tree, stack_hits
from lode.engine.evidence.secret_mask import mask_secrets
from lode.engine.integrations import connector_for
from lode.engine.investigation_events import finish_operation, progress_operation, start_operation
from lode.integration_policy import normalize_integration_config


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


def _clone_with_default_fallback(
    repo: GitRepo,
    requested_ref: str,
    root: Path,
) -> tuple[Path, str, str, bool]:
    """Resolve one repository to an immutable SHA.

    An alert-level deployment SHA normally belongs to the application that
    emitted the alert, not necessarily every repository bound to that
    application. If that SHA is absent from this repository, product policy
    treats the repository's default branch as its current incident baseline.
    Clone or default-branch failures still propagate as collection failures.
    """
    root.mkdir(parents=True, exist_ok=True)
    checkout = root / f"repo-{repo.id}"
    _git(
        ["clone", "--filter=blob:none", "--no-checkout", repo.repo_url, str(checkout)],
        timeout=settings.evidence_git_clone_timeout_seconds,
    )
    resolved_ref = requested_ref
    used_fallback = False
    try:
        _git(
            ["fetch", "--depth", "1", "origin", requested_ref],
            timeout=settings.evidence_git_clone_timeout_seconds,
            cwd=checkout,
        )
    except subprocess.CalledProcessError:
        if requested_ref == repo.default_branch:
            raise
        resolved_ref = repo.default_branch
        used_fallback = True
        _git(
            ["fetch", "--depth", "1", "origin", resolved_ref],
            timeout=settings.evidence_git_clone_timeout_seconds,
            cwd=checkout,
        )
    _git(["checkout", "--force", "FETCH_HEAD"], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)
    sha = _git(["rev-parse", "HEAD"], timeout=settings.evidence_git_clone_timeout_seconds, cwd=checkout)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Git did not resolve an immutable 40-character revision")
    return checkout, sha, resolved_ref, used_fallback


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
    repos = (
        await session.execute(
            select(GitRepo)
            .join(ApplicationRepo, ApplicationRepo.repo_id == GitRepo.id)
            .where(ApplicationRepo.application_id == investigation.application_id)
            .order_by(ApplicationRepo.id)
        )
    ).scalars().all()
    discovery = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="repository.discovery",
        actor="engine",
        title="发现源码仓库",
        purpose="枚举管理员绑定到当前应用的只读仓库",
        input_summary={"application_id": investigation.application_id},
        message="正在读取应用的仓库绑定",
        commit=True,
    )
    await finish_operation(
        session,
        discovery,
        status="succeeded" if repos else "blocked",
        result_summary=f"发现 {len(repos)} 个仓库" if repos else "应用没有配置源码仓库",
        message="仓库发现完成" if repos else "没有可调查的源码仓库",
        metrics={"repository_count": len(repos)},
        commit=True,
    )
    if not repos:
        return []

    # The effective incident baseline is always immutable. Prefer the deployed
    # ref; when it is absent, product policy treats the repository default
    # branch HEAD as the current incident version.
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
        for repo in repos:
            requested_ref = investigation.deployment_sha or repo.default_branch
            revision = (
                await session.execute(
                    select(SourceRevision).where(
                        SourceRevision.investigation_id == investigation.id,
                        SourceRevision.repo_id == repo.id,
                        SourceRevision.role == requested_role,
                    )
                )
            ).scalars().first()
            archived = (
                await session.execute(
                    select(EvidenceArtifact.id).where(
                        EvidenceArtifact.investigation_id == investigation.id,
                        EvidenceArtifact.source_id == repo.id,
                        EvidenceArtifact.source_kind.in_(["git", "git_context"]),
                    )
                )
            ).scalars().all()
            if revision and revision.status == "resolved" and archived:
                artifact_ids.extend(int(item) for item in archived)
                continue
            if revision is None:
                revision = SourceRevision(
                    investigation_id=investigation.id,
                    repo_id=repo.id,
                    role=requested_role,
                    requested_ref=requested_ref,
                    resolution_basis="incident_deployment" if investigation.deployment_sha else "default_branch_assumed_current",
                    origin_url=repo.repo_url,
                    status="queued",
                )
                session.add(revision)
                await session.flush()
            else:
                revision.status = "queued"
                revision.failure_detail = None
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
                    "fallback_ref": repo.default_branch,
                },
                config={"repo_url": repo.repo_url, "default_branch": repo.default_branch},
            )
            operation = await start_operation(
                session,
                investigation_id=investigation.id,
                step_id=step.id,
                kind="git.checkout",
                actor="collector",
                title=f"检出 {repo.name}",
                purpose="解析并固定不可变源码版本，供后续代码范围校验",
                input_summary={
                    "repo_id": repo.id,
                    "revision_role": requested_role,
                    "requested_ref": requested_ref,
                    "fallback_ref": repo.default_branch,
                },
                message=f"正在解析 {repo.name} 的源码版本",
                commit=True,
            )
            try:
                checkout, sha, resolved_ref, used_fallback = await asyncio.to_thread(
                    _clone_with_default_fallback,
                    repo,
                    requested_ref,
                    sandbox / str(repo.id),
                )
                revision.resolved_sha = sha
                revision.status = "resolved"
                revision.resolution_basis = (
                    "default_branch_after_unresolved_deployment"
                    if used_fallback
                    else "incident_deployment"
                    if investigation.deployment_sha
                    else "default_branch_assumed_current"
                )
                if used_fallback:
                    await progress_operation(
                        session,
                        operation,
                        message=f"请求版本不属于 {repo.name}，已按策略使用默认分支 {resolved_ref}",
                        detail={
                            "requested_ref": requested_ref,
                            "resolved_ref": resolved_ref,
                            "resolution_basis": revision.resolution_basis,
                        },
                        commit=True,
                    )
                await finish_operation(
                    session,
                    operation,
                    status="succeeded",
                    result_summary=(
                        f"请求版本不属于此仓库，已将默认分支 {resolved_ref} 固定为 {sha}"
                        if used_fallback
                        else f"已固定源码版本 {sha}"
                    ),
                    message="不可变源码版本已就绪",
                    metrics={
                        "requested_ref": requested_ref,
                        "resolved_ref": resolved_ref,
                        "resolved_sha": sha,
                        "revision_role": requested_role,
                        "resolution_basis": revision.resolution_basis,
                        "fallback_used": used_fallback,
                    },
                    commit=True,
                )
            except Exception as exc:
                revision.status = "failed"
                revision.failure_detail = str(exc)[:1_000]
                collection.status = "failed"
                collection.failure_code = type(exc).__name__
                collection.failure_detail = str(exc)[:1_000]
                collection.finished_at = datetime.now(UTC)
                await finish_operation(
                    session,
                    operation,
                    status="failed",
                    result_summary="无法解析请求的源码版本",
                    message="源码版本解析失败",
                    failure=exc,
                    commit=True,
                )
                continue

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
                    metadata_={"revision_role": requested_role, "revision": sha, "resolution_basis": revision.resolution_basis, "repo_id": repo.id, "path": relative, "start_line": 1, "end_line": max(1, len(raw.splitlines())), "language": _language(relative), "selection_basis": "repository_context", "secret_categories": categories},
                )
                session.add(context)
                await session.flush()
                artifact_ids.append(context.id)
                context_count += 1
                if remaining_bytes <= 0:
                    break
            collection.status = "succeeded"
            collection.artifact_count = len(repo_artifacts) + context_count
            collection.metadata_ = {"resolved_sha": sha, "resolved_ref": resolved_ref, "resolution_basis": revision.resolution_basis, "revision_role": requested_role, "stack_hits": len(exact_hits), "lexical_candidates": len(lexical_hits), "related_symbols": len(relationship_hits)}
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


def _secret(ref: str | None) -> str:
    if not ref:
        return ""
    if ref.startswith("env://"):
        import os

        return os.environ.get(ref[6:], "")
    return decrypt_secret(ref) or ""


def _http_json(url: str, *, headers: dict[str, str], timeout: int) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


async def collect_connector_evidence(
    session,
    *,
    investigation: Investigation,
    step: InvestigationStep,
    connector: EvidenceConnector,
) -> list[int]:
    """Execute one server-registered connector capability and await it fully."""
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
        if connector.kind in {"loki", "prometheus", "tempo"}:
            base_url = str(config.get("base_url") or "").rstrip("/")
            headers = {"Authorization": f"Bearer {_secret(connector.secret_ref)}"} if _secret(connector.secret_ref) else {}
            if connector.kind == "tempo":
                if not investigation.trace_id:
                    raise ValueError("incident input has no trace_id")
                url = f"{base_url}/api/traces/{urllib.parse.quote(investigation.trace_id, safe='')}"
                artifact_type = "trace"
            else:
                query_key = "logql" if connector.kind == "loki" else "promql"
                query = str(config.get("query") or selector.get(query_key) or "")
                if not base_url or not query:
                    raise ValueError(f"administrator-approved {query_key} selector is required")
                endpoint = "/loki/api/v1/query_range" if connector.kind == "loki" else "/api/v1/query_range"
                params = {"query": query, "start": investigation.window_started_at.isoformat(), "end": investigation.window_finished_at.isoformat()}
                url = f"{base_url}{endpoint}?{urllib.parse.urlencode(params)}"
                artifact_type = "log" if connector.kind == "loki" else "metric"
            payload = await asyncio.to_thread(_http_json, url, headers=headers, timeout=connector.collection_budget_seconds)
            locator = base_url
            summary = f"{connector.kind} incident-window snapshot"
        elif connector.kind == "postgres":
            raise ValueError("no approved executable PostgreSQL diagnostic profile")
        else:
            normalized = normalize_integration_config(connector.kind, config)
            client = connector_for(connector.kind)
            credential = _secret(connector.secret_ref)
            await asyncio.wait_for(client.verify_readonly(normalized, credential), timeout=connector.collection_budget_seconds)
            snapshot = await asyncio.wait_for(client.collect_snapshot(normalized, credential), timeout=connector.collection_budget_seconds)
            payload = snapshot.payload
            locator = snapshot.locator
            summary = snapshot.summary
            artifact_type = "dependency"
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
