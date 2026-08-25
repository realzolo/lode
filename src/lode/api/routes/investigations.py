"""Dynamic, evidence-first investigation API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import AsyncIterator, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.ai_output import AI_OUTPUT_LANGUAGE_SETTING_KEY, normalize_ai_output_language
from lode.api.deps import assert_app_perm, permitted_app_ids, require_user
from lode.api.schemas import InvestigationFollowUpIn
from lode.config import settings
from lode.db.models.alert import Alert
from lode.db.models.application import Application
from lode.db.models.investigation import (
    EvidenceArtifact,
    Hypothesis,
    Investigation,
    InvestigationAiInvocation,
    InvestigationEvidenceLink,
    InvestigationFinding,
    InvestigationFindingEdge,
    InvestigationExecutionEvent,
    InvestigationJob,
    InvestigationPlanNode,
    InvestigationPlanNodeDependency,
    InvestigationPlanRevision,
    InvestigationStage,
    RemediationPlan,
    SourceRevision,
)
from lode.db.models.platform_setting import PlatformSetting
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine.evidence.secret_mask import mask_secrets
from lode.engine.investigation_events import append_execution_event


router = APIRouter(prefix="/investigations", tags=["investigations"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _run(session: AsyncSession, public_id: str) -> Investigation:
    row = (await session.execute(select(Investigation).where(Investigation.public_id == public_id))).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="investigation not found")
    return row


def _diff_view(patch: str) -> dict:
    """Turn archived, redacted unified diff text into read-only editor inputs."""
    before: list[str] = []
    after: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("diff --git ", "index ", "--- ", "+++ ", "@@ ")):
            continue
        if line.startswith("-"):
            before.append(line[1:])
        elif line.startswith("+"):
            after.append(line[1:])
        else:
            value = line[1:] if line.startswith(" ") else line
            before.append(value)
            after.append(value)
    return {"mode": "diff", "language": "plaintext", "before": "\n".join(before), "after": "\n".join(after)}


def _localized(language: str, chinese: str, english: str) -> str:
    return chinese if language == "zh" else english


def _event_display(row: InvestigationExecutionEvent, language: str) -> dict:
    """Build the safe event copy used by the visible live workbench."""
    detail = row.detail or {}
    phase = row.phase
    tone = "active" if phase in {"started", "progress"} else "success" if phase == "succeeded" else "danger" if phase == "failed" else "warning" if phase in {"partial", "blocked", "not_configured", "canceled"} else "neutral"
    labels = {
        "repository_discovery": ("检查已绑定仓库", "Checking bound repositories"),
        "search_terms": ("整理故障检索词", "Preparing incident search terms"),
        "git_clone": ("读取只读仓库", "Reading the read-only repository"),
        "git_fetch": ("获取指定版本", "Fetching the requested revision"),
        "git_checkout": ("定位固定版本", "Checking out the fixed revision"),
        "context_discovery": ("发现项目上下文", "Finding project context"),
        "context_read": ("读取项目上下文", "Reading project context"),
        "source_search": ("检索相关源码", "Searching relevant source"),
        "source_archive": ("归档源码片段", "Archiving source snippets"),
        "source_diff": ("比较源码版本", "Comparing source revisions"),
        "connector_collection": ("采集受控运行时证据", "Collecting bounded runtime evidence"),
        "evidence_freeze": ("整理可引用证据", "Preparing citable evidence"),
        "reasoning_updated": ("更新调查研判", "Updating investigation assessment"),
        "conclusion_updated": ("更新当前结论", "Updating the current conclusion"),
        "plan_changed": ("更新调查计划", "Updating the investigation plan"),
        "ai_usage_updated": ("AI 证据归纳", "AI evidence synthesis"),
        "ai_source_focus": ("AI 正在收敛源码线索", "AI is narrowing source-code leads"),
        "intake_validation": ("校验事故输入", "Validating incident input"),
        "scope_resolution": ("确认调查范围", "Resolving investigation scope"),
        "alert_archive": ("归档告警证据", "Archiving alert evidence"),
        "job_enqueue": ("提交调查任务", "Queueing investigation work"),
        "follow_up_intake": ("接收补充证据", "Receiving follow-up evidence"),
        "terminal": ("调查执行结束", "Investigation execution finished"),
    }
    if row.event_type == "node_changed":
        capability = str(detail.get("capability") or "调查节点")
        completed = {"succeeded": "完成", "partial": "收敛", "blocked": "阻塞", "failed": "失败", "canceled": "取消"}.get(phase, "更新")
        headline = _localized(language, f"{capability}正在执行" if phase in {"started", "progress"} else f"{capability}已{completed}", f"{capability} is running" if phase in {"started", "progress"} else f"{capability} {phase}")
        outcome = detail.get("outcome") if isinstance(detail.get("outcome"), dict) else {}
        message = str(detail.get("message") or detail.get("objective") or outcome.get("summary") or outcome.get("conclusion") or "")
    elif row.event_type == "plan_changed":
        headline = _localized(language, "调查计划已更新", "Investigation plan updated")
        message = str(detail.get("rationale") or "")
    elif row.event_type == "reasoning_updated":
        headline = _localized(language, "AI 已根据证据更新研判", "AI updated the assessment from evidence")
        message = str(detail.get("conclusion") or "")
    elif row.event_type == "conclusion_updated":
        headline = _localized(language, "当前结论已更新", "Current conclusion updated")
        message = str(detail.get("conclusion") or "")
    elif row.event_type == "ai_usage_updated":
        headline = _localized(language, "AI 已完成本轮证据归纳", "AI completed this evidence synthesis")
        message = str(detail.get("summary") or "")
    elif row.event_type == "ai_source_focus":
        headline = _localized(language, "AI 正在收敛与故障相关的源码", "AI is narrowing source code related to the incident")
        message = str(detail.get("summary") or _localized(language, f"已从 {detail.get('candidate_count', 0)} 个候选中保留 {detail.get('selected_count', 0)} 个可验证代码线索。", f"Kept {detail.get('selected_count', 0)} verifiable code leads from {detail.get('candidate_count', 0)} candidates."))
    elif row.event_type == "source_search":
        headline = _localized(language, "检索与故障信号匹配的代码", "Searching code that matches the incident signals")
        message = _localized(language, f"发现 {detail.get('candidate_count', detail.get('matches', 0))} 个候选，等待 AI 按相关性收敛。", f"Found {detail.get('candidate_count', detail.get('matches', 0))} candidates for AI relevance review.")
    elif row.event_type == "source_archive":
        headline = _localized(language, "已归档经筛选的源码证据", "Archived the selected source evidence")
        message = _localized(language, f"保留 {detail.get('source_matches', 0)} 个代码片段和 {detail.get('context_files', 0)} 个项目上下文文件。", f"Kept {detail.get('source_matches', 0)} code snippets and {detail.get('context_files', 0)} project-context files.")
    else:
        headline = _localized(language, *labels.get(row.event_type, ("记录调查操作", "Recording investigation operation")))
        message = str(detail.get("message") or "")
    message, _ = mask_secrets(message)
    model_backed = detail.get("status") not in {"fallback", "failed"} and detail.get("engine") != "deterministic_failure_boundary"
    actor = "ai" if row.event_type in {"ai_usage_updated", "ai_source_focus", "reasoning_updated"} and model_backed else "collector" if row.event_type in {"connector_collection", "repository_discovery", "git_clone", "git_fetch", "git_checkout", "context_discovery", "context_read", "source_search", "source_archive", "source_diff"} else "engine"
    return {"actor": actor, "headline": headline, "message": message[:500], "tone": tone, "evidence_refs": [item for item in (row.artifact_refs or []) if isinstance(item, int)]}


def _event_payload(row: InvestigationExecutionEvent, node_public_id: str | None, language: str) -> dict:
    return {
        "sequence": row.sequence,
        "type": row.event_type,
        "phase": row.phase,
        "node_id": node_public_id,
        "operation_id": row.operation_id,
        "display": _event_display(row, language),
        "detail": row.detail,
        "artifact_refs": row.artifact_refs,
        "occurred_at": row.occurred_at,
    }


def _current_activity(events: list[InvestigationExecutionEvent], node_public_ids: dict[int, str], language: str) -> dict | None:
    if not events:
        return None
    terminal = {"succeeded", "partial", "blocked", "failed", "not_configured", "canceled"}
    latest_by_operation: dict[str, InvestigationExecutionEvent] = {}
    for event in events:
        latest_by_operation[event.operation_id] = event
    active = [event for event in latest_by_operation.values() if event.phase not in terminal]
    event = max(active or events, key=lambda item: item.sequence)
    return {**_event_payload(event, node_public_ids.get(event.node_id), language), "is_running": event.phase not in terminal}


def _artifact(row: EvidenceArtifact) -> dict:
    metadata = row.metadata_ or {}
    payload = {
        "id": row.id,
        "type": row.artifact_type,
        "source": row.source_kind,
        "locator": row.locator,
        "content_hash": row.content_hash,
        "excerpt": row.redacted_excerpt,
        "metadata": metadata,
        "collected_at": row.collected_at,
    }
    if row.artifact_type == "source_file":
        match_line = metadata.get("line")
        snippet_start = metadata.get("snippet_start_line")
        snippet_end = metadata.get("snippet_end_line")
        if all((isinstance(metadata.get("path"), str), isinstance(metadata.get("sha"), str), isinstance(match_line, int), isinstance(snippet_start, int), isinstance(snippet_end, int))):
            payload["code"] = {
                "mode": "source",
                "language": str(metadata.get("language") or "plaintext"),
                "content": row.redacted_excerpt,
                "highlight_line": match_line - snippet_start + 1,
                "anchor": {
                    "path": metadata["path"],
                    "revision": metadata["sha"],
                    "snippet_start_line": snippet_start,
                    "snippet_end_line": snippet_end,
                    "match_line": match_line,
                },
            }
    elif row.artifact_type == "source_diff":
        incident_sha = metadata.get("incident_sha")
        latest_sha = metadata.get("latest_sha")
        if isinstance(incident_sha, str) and isinstance(latest_sha, str):
            payload["code"] = {
                **_diff_view(row.redacted_excerpt),
                "revisions": {"incident": incident_sha, "latest": latest_sha},
            }
    return payload


def _group_operations(events: list[InvestigationExecutionEvent], key_for: Callable[[InvestigationExecutionEvent], int | None], *, language: str = "zh") -> dict[int, list[dict]]:
    grouped: dict[int, dict[str, dict]] = {}
    for event in events:
        key = key_for(event)
        if key is None:
            continue
        bucket = grouped.setdefault(key, {})
        operation = bucket.setdefault(event.operation_id, {"id": event.operation_id, "type": event.event_type, "status": "running", "collection_id": event.collection_id, "started_at": None, "finished_at": None, "detail": {}, "artifact_refs": [], "sequence": event.sequence, "display": _event_display(event, language)})
        operation["detail"] = {**operation["detail"], **(event.detail or {})}
        operation["artifact_refs"] = list(dict.fromkeys([*operation["artifact_refs"], *(event.artifact_refs or [])]))
        operation["display"] = _event_display(event, language)
        if event.phase == "started":
            operation["started_at"] = event.occurred_at
        elif event.phase != "progress":
            operation["status"] = event.phase
            operation["finished_at"] = event.occurred_at
            operation["sequence"] = event.sequence
    return {group_id: sorted(rows.values(), key=lambda row: row["sequence"]) for group_id, rows in grouped.items()}


def _operations(events: list[InvestigationExecutionEvent]) -> dict[int, list[dict]]:
    """Legacy test helper: group append-only facts by their collector stage."""
    return _group_operations(events, lambda event: event.stage_id)


def _ai_usage(rows: list[InvestigationAiInvocation]) -> dict:
    provider = [row for row in rows if row.token_source == "provider"]
    estimated = [row for row in rows if row.token_source == "estimated"]
    actual_calls = [row for row in rows if row.error_code not in {"model_not_configured", "api_key_unavailable"}]

    def total(name: str) -> int:
        return sum(int(getattr(row, name) or 0) for row in rows)

    return {
        "participating_node_count": len({row.node_id for row in rows if row.status == "succeeded" and row.node_id is not None}),
        "call_count": len(actual_calls),
        "total_latency_ms": total("latency_ms"),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "token_breakdown": {
            "provider_exact": {"calls": len(provider), "input_tokens": sum(int(row.input_tokens or 0) for row in provider), "output_tokens": sum(int(row.output_tokens or 0) for row in provider), "total_tokens": sum(int(row.total_tokens or 0) for row in provider)},
            "local_estimated": {"calls": len(estimated), "input_tokens": sum(int(row.input_tokens or 0) for row in estimated), "output_tokens": sum(int(row.output_tokens or 0) for row in estimated), "total_tokens": sum(int(row.total_tokens or 0) for row in estimated)},
        },
        "calls": [
            {
                "purpose": row.purpose,
                "provider": row.provider,
                "model": row.model,
                "status": row.status,
                "latency_ms": row.latency_ms,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "total_tokens": row.total_tokens,
                "token_source": row.token_source,
                "error_code": row.error_code,
                "summary": row.summary,
                "evidence_refs": row.evidence_refs,
            }
            for row in rows
        ],
    }


def _investigation_brief(nodes: list[InvestigationPlanNode]) -> dict | None:
    """Return the persisted new-workbench presentation layer only."""
    for node in reversed(nodes):
        if node.capability in {"reasoning", "planning"} and isinstance((node.outcome or {}).get("brief"), dict):
            return node.outcome["brief"]
    return None


def _is_workbench_v2_brief(brief: object) -> bool:
    """Reject incomplete historical briefs instead of adapting them in the UI."""
    if not isinstance(brief, dict) or not isinstance(brief.get("headline"), str) or not isinstance(brief.get("summary"), str):
        return False

    def item(value: object) -> bool:
        return isinstance(value, dict) and isinstance(value.get("text"), str) and isinstance(value.get("evidence_refs"), list) and all(isinstance(ref, int) for ref in value["evidence_refs"])

    direct_cause = brief.get("direct_cause")
    return (
        item(direct_cause)
        and direct_cause.get("status") in {"confirmed", "not_proven"}
        and all(isinstance(brief.get(key), list) and all(item(entry) for entry in brief[key]) for key in ("confirmed", "impact", "uncertain"))
        and item(brief.get("next_step"))
    )


def _sse(event: str, payload: dict, event_id: int | None = None) -> str:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


async def _stream_events(session: AsyncSession, investigation_id: int, after: int) -> list[dict]:
    rows = (
        await session.execute(
            select(InvestigationExecutionEvent)
            .where(InvestigationExecutionEvent.investigation_id == investigation_id)
            .where(InvestigationExecutionEvent.sequence > after)
            .order_by(InvestigationExecutionEvent.sequence)
        )
    ).scalars().all()
    node_ids = {row.node_id for row in rows if row.node_id is not None}
    public_ids: dict[int, str] = {}
    if node_ids:
        public_ids = {
            row.id: row.public_id
            for row in (
                await session.execute(select(InvestigationPlanNode).where(InvestigationPlanNode.id.in_(node_ids)))
            ).scalars().all()
        }
    run = await session.get(Investigation, investigation_id)
    language = run.output_language if run is not None else "zh"
    return [_event_payload(row, public_ids.get(row.node_id), language) for row in rows]


@router.get("")
async def list_investigations(user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> list[dict]:
    user = await session.get(User, user_id)
    app_ids = await permitted_app_ids(session, user_id, user.role)
    query = select(Investigation, Application.name, Alert.title, Alert.level).join(Application, Application.id == Investigation.application_id).outerjoin(Alert, Alert.id == Investigation.alert_id).order_by(Investigation.created_at.desc())
    if app_ids is not None:
        query = query.where(Investigation.application_id.in_(app_ids))
    return [{"id": run.public_id, "application_id": run.application_id, "application_name": app_name, "title": title or "", "level": level or "WARNING", "status": run.status, "result_state": run.result_state, "review_required": run.review_required, "confidence": float(run.confidence) if run.confidence is not None else None, "conclusion": run.conclusion, "created_at": run.created_at} for run, app_name, title, level in (await session.execute(query)).all()]


@router.get("/{investigation_id}/stream")
async def stream_investigation(
    investigation_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Replay durable investigation events then keep the stream live.

    The UI opens this with fetch rather than native EventSource so the existing
    bearer-token auth contract remains intact across the cross-origin API.
    """
    run = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    await assert_app_perm(session, user, run.application_id, "read")
    try:
        last_event_id = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        last_event_id = 0
    cursor = max(after, last_event_id)
    run_id = run.id
    # The generator uses short-lived sessions for every replay poll. Release
    # the request-scoped auth session before keeping a browser connection open.
    await session.close()

    async def generate() -> AsyncIterator[str]:
        nonlocal cursor
        heartbeat = 0
        async with AsyncSessionLocal() as stream_session:
            snapshot_run = await stream_session.get(Investigation, run_id)
            latest = (
                await stream_session.execute(
                    select(InvestigationExecutionEvent.sequence)
                    .where(InvestigationExecutionEvent.investigation_id == run_id)
                    .order_by(InvestigationExecutionEvent.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none() or 0
            yield _sse("snapshot", {
                "sequence": latest,
                "status": snapshot_run.status if snapshot_run else "failed",
                "result_state": snapshot_run.result_state if snapshot_run else "unavailable",
                "conclusion": snapshot_run.conclusion if snapshot_run else None,
                "conclusion_version": snapshot_run.conclusion_version if snapshot_run else 0,
            }, latest)
        while True:
            if await request.is_disconnected():
                return
            async with AsyncSessionLocal() as stream_session:
                events = await _stream_events(stream_session, run_id, cursor)
                live_run = await stream_session.get(Investigation, run_id)
            if events:
                for event in events:
                    cursor = event["sequence"]
                    yield _sse("investigation_event", event, cursor)
                heartbeat = 0
                continue
            if live_run is None or live_run.status in {"completed", "failed"}:
                yield _sse("terminal", {"sequence": cursor, "status": live_run.status if live_run else "failed"}, cursor)
                return
            heartbeat += 1
            if heartbeat >= 15:
                yield ": keepalive\n\n"
                heartbeat = 0
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str, user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> dict:
    run = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    await assert_app_perm(session, user, run.application_id, "read")
    app = await session.get(Application, run.application_id)
    alert = await session.get(Alert, run.alert_id)
    nodes = (await session.execute(select(InvestigationPlanNode).where(InvestigationPlanNode.investigation_id == run.id).order_by(InvestigationPlanNode.id))).scalars().all()
    revisions = (await session.execute(select(InvestigationPlanRevision).where(InvestigationPlanRevision.investigation_id == run.id).order_by(InvestigationPlanRevision.revision))).scalars().all()
    dependencies = (await session.execute(select(InvestigationPlanNodeDependency).join(InvestigationPlanNode, InvestigationPlanNode.id == InvestigationPlanNodeDependency.node_id).where(InvestigationPlanNode.investigation_id == run.id))).scalars().all()
    events = (await session.execute(select(InvestigationExecutionEvent).where(InvestigationExecutionEvent.investigation_id == run.id).order_by(InvestigationExecutionEvent.sequence))).scalars().all()
    artifacts = (await session.execute(select(EvidenceArtifact).join(InvestigationEvidenceLink, InvestigationEvidenceLink.artifact_id == EvidenceArtifact.id).where(InvestigationEvidenceLink.investigation_id == run.id).order_by(EvidenceArtifact.id))).scalars().all()
    links = (await session.execute(select(InvestigationEvidenceLink).where(InvestigationEvidenceLink.investigation_id == run.id))).scalars().all()
    source_revisions = (await session.execute(select(SourceRevision).where(SourceRevision.investigation_id == run.id).order_by(SourceRevision.id))).scalars().all()
    hypotheses = (await session.execute(select(Hypothesis).where(Hypothesis.investigation_id == run.id).order_by(Hypothesis.rank))).scalars().all()
    findings = (await session.execute(select(InvestigationFinding).where(InvestigationFinding.investigation_id == run.id).order_by(InvestigationFinding.ordinal))).scalars().all()
    finding_edges = (await session.execute(select(InvestigationFindingEdge).where(InvestigationFindingEdge.investigation_id == run.id).order_by(InvestigationFindingEdge.id))).scalars().all()
    invocations = (await session.execute(select(InvestigationAiInvocation).where(InvestigationAiInvocation.investigation_id == run.id).order_by(InvestigationAiInvocation.id))).scalars().all()
    remediation = (await session.execute(select(RemediationPlan).where(RemediationPlan.investigation_id == run.id))).scalars().first()
    job = (await session.execute(select(InvestigationJob).where(InvestigationJob.investigation_id == run.id).order_by(InvestigationJob.id.desc()).limit(1))).scalars().first()
    node_by_stage = {row.stage_id: row.id for row in nodes if row.stage_id is not None}
    node_operations = _group_operations(events, lambda event: event.node_id or node_by_stage.get(event.stage_id), language=run.output_language)
    dependencies_by_node: dict[int, list[int]] = {}
    for row in dependencies:
        dependencies_by_node.setdefault(row.node_id, []).append(row.depends_on_node_id)
    invocations_by_node: dict[int, list[InvestigationAiInvocation]] = {}
    for row in invocations:
        if row.node_id is not None:
            invocations_by_node.setdefault(row.node_id, []).append(row)
    latest_catalog = revisions[-1].capability_catalog if revisions else {}
    evidence_counts: dict[str, int] = {}
    for artifact in artifacts:
        evidence_counts[artifact.artifact_type] = evidence_counts.get(artifact.artifact_type, 0) + 1
    scope = dict(run.scope or {})
    scope_sources = scope.pop("scope_sources", {})
    parent = await session.get(Investigation, run.parent_investigation_id) if run.parent_investigation_id else None
    successor = await session.get(Investigation, run.superseded_by_investigation_id) if run.superseded_by_investigation_id else None
    node_public_ids = {row.id: row.public_id for row in nodes}
    revision_numbers = {row.id: row.revision for row in revisions}
    brief = _investigation_brief(nodes)
    if brief is not None and not _is_workbench_v2_brief(brief):
        raise HTTPException(status_code=409, detail="investigation does not satisfy the Workbench 2.0 display contract; reinvestigate it")
    return {
        "id": run.public_id,
        "application": {"id": run.application_id, "name": app.name if app else ""},
        "alert": {"title": alert.title, "level": alert.level, "topic": alert.topic, "error_message": alert.error_message} if alert else None,
        "status": run.status,
        "result_state": run.result_state,
        "review_required": run.review_required,
        "review_reasons": run.review_reasons,
        "audit_status": run.audit_status,
        "engine_version": run.engine_version,
        "output_language": run.output_language,
        "scope": {"service": run.service_name, "environment": run.environment, "trace_id": run.trace_id, "deployment_sha": run.deployment_sha, "window_started_at": run.window_started_at, "window_finished_at": run.window_finished_at, "sources": scope_sources, "context": scope},
        "conclusion": run.conclusion,
        "brief": brief,
        "confidence": float(run.confidence) if run.confidence is not None else None,
        "conclusion_version": run.conclusion_version,
        "superseded_by_investigation_id": successor.public_id if successor else None,
        "capability_catalog": latest_catalog,
        "plan_history": [{"revision": row.revision, "decision": row.decision, "wave": row.wave, "trigger_node_id": node_public_ids.get(row.trigger_node_id), "rationale": row.rationale, "change_set": row.change_set, "evidence_refs": row.evidence_refs, "created_at": row.created_at} for row in revisions],
        "nodes": [
            {
                "id": row.public_id,
                "capability": row.capability,
                "plan_revision": revision_numbers.get(row.plan_revision_id, row.plan_revision_id),
                "title": row.title,
                "objective": row.objective,
                "selection_reason": row.selection_reason,
                "expected_evidence": row.expected_evidence,
                "decision_rule": row.decision_rule,
                "budget": row.budget,
                "stop_condition": row.stop_condition,
                "tool_input": row.tool_input,
                "status": row.status,
                "input_refs": row.input_refs,
                "output_refs": row.output_refs,
                "outcome": row.outcome,
                "failure_code": row.failure_code,
                "failure_detail": row.failure_detail,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "dependencies": [node_public_ids[item] for item in dependencies_by_node.get(row.id, []) if item in node_public_ids],
                "operations": node_operations.get(row.id, []),
                "ai_participated": row.ai_participated,
                "ai_usage": _ai_usage(invocations_by_node.get(row.id, [])),
            }
            for row in nodes
        ],
        "source_revisions": [{"role": row.role, "requested_ref": row.requested_ref, "resolved_sha": row.resolved_sha, "resolution_basis": row.resolution_basis, "origin_url": row.origin_url, "status": row.status, "failure_detail": row.failure_detail} for row in source_revisions],
        "evidence": [_artifact(row) for row in artifacts],
        "evidence_coverage": {"artifact_count": len(artifacts), "by_type": evidence_counts, "open_requirements": [{"text": row.text, "rationale": row.rationale} for row in findings if row.kind == "evidence_gap" and row.status == "required"]},
        "reasoning_path": [{"id": row.id, "kind": row.kind, "status": row.status, "text": row.text, "rationale": row.rationale, "confidence": float(row.confidence) if row.confidence is not None else None, "evidence_refs": row.evidence_refs} for row in findings],
        "reasoning_edges": [{"from": row.from_finding_id, "to": row.to_finding_id, "relation": row.relation, "evidence_refs": row.evidence_refs} for row in finding_edges],
        "ai_usage": _ai_usage(invocations),
        "inheritance": {"parent_investigation_id": parent.public_id if parent else None, "superseded_by_investigation_id": successor.public_id if successor else None, "evidence_members": [{"artifact_id": row.artifact_id, "relation": row.relation} for row in links]},
        "hypotheses": [{"rank": row.rank, "status": row.status, "text": row.text, "confidence": float(row.confidence), "evidence_refs": row.evidence_refs} for row in hypotheses],
        "remediation": None if remediation is None else {"summary": remediation.summary, "risk_level": remediation.risk_level, "evidence_refs": remediation.evidence_refs, "preconditions": remediation.preconditions, "steps": remediation.steps, "verification": remediation.verification, "rollback": remediation.rollback, "agent_prompt": remediation.agent_prompt},
        "job": None if job is None else {"status": job.status, "attempt": job.attempt, "max_attempts": job.max_attempts, "last_error_code": job.last_error_code, "last_error_detail": job.last_error_detail},
        "execution": {"current_activity": _current_activity(events, node_public_ids, run.output_language), "operation_count": len({event.operation_id for event in events})},
        "live_timeline": [
            _event_payload(row, node_public_ids.get(row.node_id), run.output_language)
            for row in events[-80:]
        ],
        "event_cursor": events[-1].sequence if events else 0,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
    }


async def _create_inherited_investigation(session: AsyncSession, *, prior: Investigation, user_id: int, body: InvestigationFollowUpIn, trigger: str) -> Investigation:
    active = (await session.execute(select(Investigation).where(Investigation.incident_id == prior.incident_id).where(Investigation.status.in_(["queued", "running"])).limit(1))).scalars().first()
    if active is not None:
        raise HTTPException(status_code=409, detail="an investigation is already active for this incident")
    setting = await session.get(PlatformSetting, AI_OUTPUT_LANGUAGE_SETTING_KEY)
    language = normalize_ai_output_language(setting.value if setting is not None else None)
    patch = body.scope_patch.model_dump(exclude_none=True) if body.scope_patch else {}
    scope = dict(prior.scope or {})
    sources = dict(scope.get("scope_sources") or {})
    for key in patch:
        sources[key] = f"follow_up.user.{user_id}"
    scope.update({"trigger": trigger, "requested_by": user_id, "scope_sources": sources, "parent_investigation": prior.public_id})
    run = Investigation(application_id=prior.application_id, alert_id=prior.alert_id, incident_id=prior.incident_id, parent_investigation_id=prior.id, trigger_signature=prior.trigger_signature, status="queued", output_language=language, service_name=patch.get("service_name", prior.service_name), environment=patch.get("environment", prior.environment), trace_id=patch.get("trace_id", prior.trace_id), deployment_sha=patch.get("deployment_sha", prior.deployment_sha), window_started_at=prior.window_started_at, window_finished_at=prior.window_finished_at, scope=scope)
    session.add(run)
    await session.flush()
    stage = InvestigationStage(investigation_id=run.id, stage_type="ingest", status="running", order_index=0, input={"trigger": trigger, "parent": prior.public_id}, output={}, started_at=datetime.now(UTC))
    session.add(stage)
    await session.flush()
    operation = await append_execution_event(session, investigation_id=run.id, stage_id=stage.id, event_type="follow_up_intake", phase="started", detail={"parent_investigation": prior.public_id, "evidence_count": len(body.evidence), "scope_patch": list(patch)})
    inherited_ids = (await session.execute(select(InvestigationEvidenceLink.artifact_id).where(InvestigationEvidenceLink.investigation_id == prior.id))).scalars().all()
    if not inherited_ids:
        inherited_ids = (await session.execute(select(EvidenceArtifact.id).where(EvidenceArtifact.investigation_id == prior.id))).scalars().all()
    for artifact_id in inherited_ids:
        session.add(InvestigationEvidenceLink(investigation_id=run.id, artifact_id=artifact_id, relation="inherited"))
    new_refs: list[int] = []
    for item in body.evidence:
        excerpt, categories = mask_secrets(item.content)
        artifact = EvidenceArtifact(investigation_id=run.id, collection_id=None, artifact_type="operator_input", source_kind="operator", source_id=user_id, locator=item.locator or f"follow-up://{run.public_id}/{item.kind}", content_hash=hashlib.sha256(item.content.encode()).hexdigest(), redacted_excerpt=excerpt[:20_000], metadata_={"declared_kind": item.kind, "submitted_by": user_id, "time_scope": "operator_follow_up", "secret_categories": categories})
        session.add(artifact)
        await session.flush()
        new_refs.append(artifact.id)
        session.add(InvestigationEvidenceLink(investigation_id=run.id, artifact_id=artifact.id, relation="manual"))
    job = InvestigationJob(incident_id=run.incident_id, investigation_id=run.id, status="queued", max_attempts=settings.job_max_attempts)
    session.add(job)
    await session.flush()
    await append_execution_event(session, investigation_id=run.id, stage_id=stage.id, event_type="follow_up_intake", phase="succeeded", operation_id=operation, detail={"inherited_artifact_count": len(inherited_ids), "manual_artifact_count": len(new_refs), "scope_patch": list(patch)}, artifact_refs=new_refs)
    stage.status = "succeeded"
    stage.finished_at = datetime.now(UTC)
    stage.output = {"summary": "Inherited evidence and bounded operator input were archived.", "inherited_artifact_count": len(inherited_ids), "manual_artifact_count": len(new_refs)}
    await session.commit()
    return run


@router.post("/{investigation_id}/follow-ups", status_code=201)
async def create_follow_up(investigation_id: str, body: InvestigationFollowUpIn, user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> dict:
    prior = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    await assert_app_perm(session, user, prior.application_id, "analyze")
    run = await _create_inherited_investigation(session, prior=prior, user_id=user_id, body=body, trigger="manual_follow_up")
    return {"id": run.public_id, "status": run.status, "parent_investigation_id": prior.public_id}


@router.post("/{investigation_id}/reanalyze", status_code=201)
async def reinvestigate(investigation_id: str, user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> dict:
    prior = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    await assert_app_perm(session, user, prior.application_id, "analyze")
    run = await _create_inherited_investigation(session, prior=prior, user_id=user_id, body=InvestigationFollowUpIn(), trigger="manual_reanalysis")
    return {"id": run.public_id, "status": run.status, "parent_investigation_id": prior.public_id}
