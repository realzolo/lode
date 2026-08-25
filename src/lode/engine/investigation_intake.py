"""Shared Kafka and operator intake for the canonical investigation input."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from lode.config import settings
from lode.db.models.investigation import (
    EvidenceArtifact,
    Investigation,
    InvestigationEvidenceLink,
    InvestigationInput,
    InvestigationJob,
    InvestigationStep,
)
from lode.engine.evidence.secret_mask import mask_secrets
from lode.engine.investigation_events import finish_operation, finish_step, start_operation, start_step


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth >= 6:
        return "[depth limit]"
    if isinstance(value, dict):
        return {str(key)[:100]: _bounded_json(child, depth=depth + 1) for key, child in list(value.items())[:100]}
    if isinstance(value, list):
        return [_bounded_json(child, depth=depth + 1) for child in value[:100]]
    if isinstance(value, str):
        return value[:20_000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:2_000]


def canonical_input_payload(
    *,
    title: str,
    severity: str,
    occurred_at: datetime,
    error_name: str,
    error_message: str,
    error_stack: str | None,
    error_cause: Any,
    error_properties: dict[str, Any],
    fields: dict[str, Any],
    scope: dict[str, Any],
) -> dict[str, Any]:
    return _bounded_json(
        {
            "title": title,
            "severity": severity,
            "occurred_at": occurred_at.isoformat(),
            "error": {
                "name": error_name,
                "message": error_message,
                "stack": error_stack,
                "cause": error_cause,
                "properties": error_properties,
            },
            "fields": fields,
            "scope": scope,
        }
    )


async def create_investigation(
    session,
    *,
    application_id: int,
    trigger_signature: str,
    source_type: str,
    title: str,
    severity: str,
    occurred_at: datetime,
    output_language: str,
    error_name: str,
    error_message: str,
    error_stack: str | None = None,
    error_cause: Any = None,
    error_properties: dict[str, Any] | None = None,
    fields: dict[str, Any] | None = None,
    service_name: str | None = None,
    environment: str | None = None,
    trace_id: str | None = None,
    deployment_sha: str | None = None,
    application_version: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    scope_sources: dict[str, str | None] | None = None,
    alert_id: int | None = None,
    incident_id: int | None = None,
    created_by: int | None = None,
) -> tuple[Investigation, InvestigationJob]:
    occurred_at = (occurred_at.replace(tzinfo=UTC) if occurred_at.tzinfo is None else occurred_at.astimezone(UTC))
    fields = _bounded_json(fields or {})
    error_properties = _bounded_json(error_properties or {})
    error_cause = _bounded_json(error_cause)
    scope = {
        "service": service_name,
        "environment": environment,
        "trace_id": trace_id,
        "deployment_sha": deployment_sha,
        "application_version": application_version,
        "source": _bounded_json(source_metadata or {}),
        "sources": scope_sources or {},
    }
    investigation = Investigation(
        application_id=application_id,
        alert_id=alert_id,
        incident_id=incident_id,
        trigger_signature=trigger_signature,
        status="queued",
        result_state="pending",
        output_language=output_language,
        service_name=service_name,
        environment=environment,
        trace_id=trace_id,
        deployment_sha=deployment_sha,
        window_started_at=occurred_at - timedelta(seconds=settings.investigation_window_before_seconds),
        window_finished_at=occurred_at + timedelta(seconds=settings.investigation_window_after_seconds),
        scope=scope,
    )
    session.add(investigation)
    await session.flush()
    investigation_input = InvestigationInput(
        investigation_id=investigation.id,
        source_type=source_type,
        title=title[:500],
        severity=severity,
        occurred_at=occurred_at,
        error_name=(error_name or "Error")[:500],
        error_message=error_message[:20_000],
        error_stack=error_stack[:50_000] if error_stack else None,
        error_cause=error_cause,
        error_properties=error_properties,
        fields=fields,
        scope=scope,
        created_by=created_by,
    )
    session.add(investigation_input)
    step = InvestigationStep(
        investigation_id=investigation.id,
        ordinal=1,
        kind="intake",
        title="规范化事故输入" if output_language == "zh" else "Normalize incident input",
        objective="保留完整错误链并建立不可变调查输入。" if output_language == "zh" else "Preserve the complete error chain as immutable investigation input.",
        selection_reason="所有调查必须从同一规范化契约开始。" if output_language == "zh" else "Every investigation starts from one canonical input contract.",
        expected_evidence="错误、堆栈、范围和来源已脱敏归档。" if output_language == "zh" else "A redacted archive of the error, stack, scope, and provenance.",
        status="queued",
    )
    session.add(step)
    await session.flush()
    await start_step(session, step)

    validation = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="input_validation",
        actor="engine",
        title="校验事故输入" if output_language == "zh" else "Validate incident input",
        purpose="验证必填字段并保留完整错误链。" if output_language == "zh" else "Validate required fields and preserve the complete error chain.",
        input_summary={"source": source_type, "severity": severity, "has_stack": bool(error_stack), "has_cause": error_cause is not None},
        message="正在校验错误消息、堆栈和发生时间。" if output_language == "zh" else "Validating the error, stack, and occurrence time.",
    )
    await finish_operation(
        session,
        validation,
        status="succeeded",
        result_summary=(f"输入有效；错误类型 {error_name or 'Error'}，堆栈 {'已提供' if error_stack else '未提供'}。" if output_language == "zh" else f"Input is valid; error type {error_name or 'Error'}, stack {'present' if error_stack else 'absent'}."),
        message="事故输入已通过校验。" if output_language == "zh" else "Incident input passed validation.",
        metrics={"message_bytes": len(error_message.encode()), "stack_bytes": len((error_stack or "").encode())},
    )

    scope_operation = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="scope_resolution",
        actor="engine",
        title="确认调查范围" if output_language == "zh" else "Resolve investigation scope",
        purpose="固化服务、环境、版本、trace 和事故时间窗。" if output_language == "zh" else "Freeze service, environment, revision, trace, and incident window.",
        input_summary={"sources": scope_sources or {}},
        message="正在解析范围字段及其来源。" if output_language == "zh" else "Resolving scope fields and provenance.",
    )
    identified = {"service": bool(service_name), "environment": bool(environment), "deployment_sha": bool(deployment_sha), "trace_id": bool(trace_id)}
    await finish_operation(
        session,
        scope_operation,
        status="succeeded",
        result_summary=(f"已识别 {sum(identified.values())}/4 个范围字段；事故窗口为前后各 {settings.investigation_window_before_seconds // 60}/{settings.investigation_window_after_seconds // 60} 分钟。" if output_language == "zh" else f"Resolved {sum(identified.values())}/4 scope fields and froze the incident window."),
        message="调查范围和字段来源已固化。" if output_language == "zh" else "Investigation scope and provenance were frozen.",
        metrics=identified,
    )

    payload = canonical_input_payload(
        title=title,
        severity=severity,
        occurred_at=occurred_at,
        error_name=error_name,
        error_message=error_message,
        error_stack=error_stack,
        error_cause=error_cause,
        error_properties=error_properties,
        fields=fields,
        scope=scope,
    )
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    redacted, categories = mask_secrets(raw)
    archive = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="input_archive",
        actor="engine",
        title="归档事故证据" if output_language == "zh" else "Archive incident evidence",
        purpose="将规范化输入脱敏、哈希并保存为不可变证据。" if output_language == "zh" else "Redact, hash, and persist the canonical input as immutable evidence.",
        input_summary={"source": source_type, "redaction_categories": categories},
        message="正在脱敏并固化规范化事故输入。" if output_language == "zh" else "Redacting and freezing the canonical incident input.",
    )
    artifact = EvidenceArtifact(
        investigation_id=investigation.id,
        collection_id=None,
        artifact_type="incident_input",
        source_kind=source_type,
        source_id=alert_id or created_by,
        locator=f"incident-input://{investigation.public_id}",
        content_hash=hashlib.sha256(raw.encode()).hexdigest(),
        redacted_excerpt=redacted[:80_000],
        metadata_={
            "time_scope": "incident_input",
            "has_stack": bool(error_stack),
            "has_cause": error_cause is not None,
            "secret_categories": categories,
            "incident_link": True,
        },
    )
    session.add(artifact)
    await session.flush()
    session.add(InvestigationEvidenceLink(investigation_id=investigation.id, artifact_id=artifact.id, relation="collected"))
    await finish_operation(
        session,
        archive,
        status="succeeded",
        result_summary=f"规范化事故输入已归档为证据 {artifact.id}。" if output_language == "zh" else f"Canonical incident input was archived as evidence {artifact.id}.",
        message="不可变事故证据已生成。" if output_language == "zh" else "Immutable incident evidence was created.",
        metrics={"artifact_id": artifact.id, "redacted": bool(categories), "bytes": len(redacted.encode())},
        evidence_refs=[artifact.id],
    )

    job = InvestigationJob(
        incident_id=incident_id,
        investigation_id=investigation.id,
        status="queued",
        max_attempts=settings.job_max_attempts,
        available_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    enqueue = await start_operation(
        session,
        investigation_id=investigation.id,
        step_id=step.id,
        kind="job_enqueue",
        actor="engine",
        title="提交调查任务" if output_language == "zh" else "Queue investigation",
        purpose="将规范化调查提交给串行执行器。" if output_language == "zh" else "Submit the normalized investigation to the sequential executor.",
        input_summary={"max_attempts": job.max_attempts},
        message="正在创建持久化调查任务。" if output_language == "zh" else "Creating the durable investigation job.",
    )
    await finish_operation(
        session,
        enqueue,
        status="succeeded",
        result_summary=f"任务 {job.public_id} 已入队，最多重试 {job.max_attempts} 次。" if output_language == "zh" else f"Job {job.public_id} was queued with at most {job.max_attempts} attempts.",
        message="调查任务已进入串行执行队列。" if output_language == "zh" else "Investigation entered the sequential execution queue.",
        metrics={"job_id": job.public_id, "max_attempts": job.max_attempts},
    )
    await finish_step(
        session,
        step,
        status="succeeded",
        result_summary="完整错误链、范围和不可变输入已归档。" if output_language == "zh" else "The complete error chain, scope, and immutable input were archived.",
        output_refs=[artifact.id],
    )
    return investigation, job
