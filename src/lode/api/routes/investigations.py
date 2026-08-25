"""V1 API for sequential, evidence-backed investigations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.ai_output import AI_OUTPUT_LANGUAGE_SETTING_KEY, normalize_ai_output_language
from lode.api.deps import assert_app_perm, permitted_app_ids, require_user
from lode.api.schemas import InvestigationCreateIn
from lode.db.models.application import Application
from lode.db.models.investigation import (
    EvidenceArtifact,
    Investigation,
    InvestigationAiInvocation,
    InvestigationCodeFinding,
    InvestigationDecision,
    InvestigationEvidenceLink,
    InvestigationInput,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationReport,
    InvestigationStep,
)
from lode.db.models.platform_setting import PlatformSetting
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine.evidence.secret_mask import mask_secrets
from lode.engine.investigation_intake import create_investigation

router = APIRouter(prefix="/investigations", tags=["investigations"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _run(session: AsyncSession, public_id: str) -> Investigation:
    row = (
        await session.execute(select(Investigation).where(Investigation.public_id == public_id))
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="investigation not found")
    return row


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if not started_at:
        return None
    return max(0, int(((finished_at or datetime.now(UTC)) - started_at).total_seconds() * 1_000))


def _artifact(row: EvidenceArtifact) -> dict:
    metadata = row.metadata_ or {}
    result = {
        "id": row.id,
        "type": row.artifact_type,
        "source": row.source_kind,
        "locator": row.locator,
        "content_hash": row.content_hash,
        "excerpt": row.redacted_excerpt,
        "metadata": metadata,
        "collected_at": row.collected_at,
    }
    if row.artifact_type == "source_file" and all(
        isinstance(metadata.get(key), expected)
        for key, expected in (("path", str), ("revision", str), ("start_line", int), ("end_line", int))
    ):
        highlight = metadata.get("highlight_line")
        result["code"] = {
            "language": metadata.get("language", "plaintext"),
            "content": row.redacted_excerpt,
            "anchor": {
                "repo_id": metadata.get("repo_id"),
                "path": metadata["path"],
                "revision": metadata["revision"],
                "revision_role": metadata.get("revision_role"),
                "symbol": metadata.get("symbol"),
                "start_line": metadata["start_line"],
                "end_line": metadata["end_line"],
            },
            "highlight_start": max(1, int(highlight or metadata["start_line"]) - metadata["start_line"] + 1),
            "highlight_end": max(1, int(highlight or metadata["start_line"]) - metadata["start_line"] + 1),
        }
    return result


def _operation(row: InvestigationOperation, events: list[InvestigationOperationEvent]) -> dict:
    return {
        "id": row.public_id,
        "step_id": row.step_id,
        "ordinal": row.ordinal,
        "kind": row.kind,
        "actor": row.actor,
        "title": row.title,
        "purpose": row.purpose,
        "input": row.input_summary,
        "status": row.status,
        "result": row.result_summary,
        "metrics": row.metrics,
        "evidence_refs": row.evidence_refs,
        "failure": {"code": row.failure_code, "detail": row.failure_detail} if row.failure_code else None,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "duration_ms": _duration_ms(row.started_at, row.finished_at),
        "events": [
            {
                "sequence": event.sequence,
                "kind": event.kind,
                "message": event.message,
                "detail": event.detail,
                "evidence_refs": event.evidence_refs,
                "occurred_at": event.occurred_at,
            }
            for event in events
        ],
    }


async def _detail(session: AsyncSession, run: Investigation) -> dict:
    application = await session.get(Application, run.application_id)
    incident_input = await session.get(InvestigationInput, run.id)
    report = await session.get(InvestigationReport, run.id)
    steps = (
        await session.execute(
            select(InvestigationStep).where(InvestigationStep.investigation_id == run.id).order_by(InvestigationStep.ordinal)
        )
    ).scalars().all()
    decisions = (
        await session.execute(
            select(InvestigationDecision).where(InvestigationDecision.investigation_id == run.id).order_by(InvestigationDecision.ordinal)
        )
    ).scalars().all()
    operations = (
        await session.execute(
            select(InvestigationOperation).where(InvestigationOperation.investigation_id == run.id).order_by(InvestigationOperation.ordinal)
        )
    ).scalars().all()
    events = (
        await session.execute(
            select(InvestigationOperationEvent).where(InvestigationOperationEvent.investigation_id == run.id).order_by(InvestigationOperationEvent.sequence)
        )
    ).scalars().all()
    evidence = (
        await session.execute(
            select(EvidenceArtifact).where(EvidenceArtifact.investigation_id == run.id).order_by(EvidenceArtifact.id)
        )
    ).scalars().all()
    code_findings = (
        await session.execute(
            select(InvestigationCodeFinding).where(InvestigationCodeFinding.investigation_id == run.id).order_by(InvestigationCodeFinding.id)
        )
    ).scalars().all()
    by_operation: dict[int, list[InvestigationOperationEvent]] = {}
    for event in events:
        by_operation.setdefault(event.operation_id, []).append(event)
    return {
        "id": run.public_id,
        "application_id": run.application_id,
        "application_name": application.name if application else "",
        "status": run.status,
        "result_state": run.result_state,
        "output_language": run.output_language,
        "scope": {**(run.scope or {}), "window_started_at": run.window_started_at, "window_finished_at": run.window_finished_at},
        "review_required": run.review_required,
        "review_reasons": run.review_reasons,
        "engine_version": run.engine_version,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "input": {
            "source_type": incident_input.source_type,
            "title": incident_input.title,
            "severity": incident_input.severity,
            "occurred_at": incident_input.occurred_at,
            "error": {
                "name": incident_input.error_name,
                "message": incident_input.error_message,
                "stack": incident_input.error_stack,
                "cause": incident_input.error_cause,
                "properties": incident_input.error_properties,
            },
            "fields": incident_input.fields,
        } if incident_input else None,
        "report": {
            "result_state": report.result_state,
            "headline": report.headline,
            "summary": report.summary,
            "incident_cause": report.incident_cause,
            "code_diagnosis": report.code_diagnosis,
            "confirmed_facts": report.confirmed_facts,
            "counter_evidence": report.counter_evidence,
            "evidence_gaps": report.evidence_gaps,
            "next_step": report.next_step,
            "evidence_refs": report.evidence_refs,
        } if report else None,
        "steps": [
            {
                "id": row.public_id,
                "db_id": row.id,
                "ordinal": row.ordinal,
                "kind": row.kind,
                "title": row.title,
                "objective": row.objective,
                "selection_reason": row.selection_reason,
                "expected_evidence": row.expected_evidence,
                "tool_name": row.tool_name,
                "tool_input": row.tool_input,
                "status": row.status,
                "input_refs": row.input_refs,
                "output_refs": row.output_refs,
                "result": row.result_summary,
                "failure": {"code": row.failure_code, "detail": row.failure_detail} if row.failure_code else None,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "duration_ms": _duration_ms(row.started_at, row.finished_at),
            }
            for row in steps
        ],
        "decisions": [
            {"id": row.id, "ordinal": row.ordinal, "after_step_id": row.after_step_id, "action": row.action, "selected_tool": row.selected_tool, "rationale": row.rationale_summary, "hypothesis": row.hypothesis_snapshot, "evidence_refs": row.evidence_refs, "created_at": row.created_at}
            for row in decisions
        ],
        "operations": [_operation(row, by_operation.get(row.id, [])) for row in operations],
        "evidence": [_artifact(row) for row in evidence],
        "code_findings": [
            {column.name: getattr(row, column.name) for column in InvestigationCodeFinding.__table__.columns if column.name not in {"investigation_id"}}
            for row in code_findings
        ],
        "event_cursor": run.event_cursor,
    }


@router.get("")
async def list_investigations(user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> list[dict]:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    app_ids = await permitted_app_ids(session, user_id, user.role)
    query = (
        select(Investigation, InvestigationInput, Application.name)
        .join(InvestigationInput, InvestigationInput.investigation_id == Investigation.id)
        .join(Application, Application.id == Investigation.application_id)
        .order_by(Investigation.created_at.desc())
    )
    if app_ids is not None:
        query = query.where(Investigation.application_id.in_(app_ids))
    return [
        {"id": run.public_id, "application_id": run.application_id, "application_name": app_name, "title": value.title, "level": value.severity, "status": run.status, "result_state": run.result_state, "review_required": run.review_required, "created_at": run.created_at}
        for run, value, app_name in (await session.execute(query)).all()
    ]


@router.post("", status_code=202)
async def create_manual_investigation(body: InvestigationCreateIn, user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> dict:
    user = await session.get(User, user_id)
    application = await session.get(Application, body.application_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    await assert_app_perm(session, user, application.id, "analyze")
    setting = await session.get(PlatformSetting, AI_OUTPUT_LANGUAGE_SETTING_KEY)
    language = normalize_ai_output_language(setting.value if setting else None)
    signature_source = "\n".join([body.error.name, body.error.message, *(body.error.stack or "").splitlines()[:3]])
    trigger_signature = hashlib.sha256(signature_source.encode()).hexdigest()
    run, job = await create_investigation(
        session,
        application_id=application.id,
        trigger_signature=trigger_signature,
        source_type="manual",
        title=body.title,
        severity=body.severity,
        occurred_at=body.occurred_at,
        output_language=language,
        error_name=body.error.name,
        error_message=body.error.message,
        error_stack=body.error.stack,
        error_cause=body.error.cause,
        error_properties=body.error.properties,
        fields={**body.fields, "attachments": [item.model_dump() for item in body.attachments]},
        service_name=body.service_name,
        environment=body.environment,
        trace_id=body.trace_id,
        deployment_sha=body.deployment_sha,
        source_metadata={"submitted_by": user_id, "attachment_count": len(body.attachments)},
        scope_sources={"service": "manual", "environment": "manual", "trace_id": "manual", "deployment_sha": "manual"},
        created_by=user_id,
    )
    for attachment in body.attachments:
        redacted, categories = mask_secrets(attachment.content)
        artifact_type = attachment.kind if attachment.kind in {"log", "trace", "dependency"} else "operator_input"
        artifact = EvidenceArtifact(
            investigation_id=run.id,
            artifact_type=artifact_type,
            source_kind="manual",
            source_id=user_id,
            locator=attachment.label,
            content_hash=hashlib.sha256(attachment.content.encode()).hexdigest(),
            redacted_excerpt=redacted,
            metadata_={"time_scope": "operator_supplied", "secret_categories": categories},
        )
        session.add(artifact)
        await session.flush()
        session.add(InvestigationEvidenceLink(investigation_id=run.id, artifact_id=artifact.id, relation="manual"))
    await session.commit()
    return {"id": run.public_id, "job_id": job.public_id, "status": "queued"}


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str, user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> dict:
    run = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    await assert_app_perm(session, user, run.application_id, "read")
    return await _detail(session, run)


@router.get("/{investigation_id}/events")
async def get_events(
    investigation_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    await assert_app_perm(session, user, run.application_id, "read")
    rows = (
        await session.execute(
            select(InvestigationOperationEvent)
            .where(InvestigationOperationEvent.investigation_id == run.id, InvestigationOperationEvent.sequence > after)
            .order_by(InvestigationOperationEvent.sequence)
            .limit(limit + 1)
        )
    ).scalars().all()
    page = rows[:limit]
    return {
        "items": [{"sequence": row.sequence, "type": f"operation.{row.kind}", "step_id": row.step_id, "operation_id": row.operation_id, "message": row.message, "detail": row.detail, "evidence_refs": row.evidence_refs, "occurred_at": row.occurred_at} for row in page],
        "next_cursor": page[-1].sequence if len(rows) > limit else None,
    }


@router.get("/{investigation_id}/audit")
async def get_audit(
    investigation_id: str,
    operation_cursor: int = Query(default=0, ge=0),
    ai_cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    await assert_app_perm(session, user, run.application_id, "read")
    operation_rows = (
        await session.execute(
            select(InvestigationOperation)
            .where(
                InvestigationOperation.investigation_id == run.id,
                InvestigationOperation.id > operation_cursor,
            )
            .order_by(InvestigationOperation.id)
            .limit(limit + 1)
        )
    ).scalars().all()
    operation_page = operation_rows[:limit]
    operation_ids = [row.id for row in operation_page]
    operation_events = (
        await session.execute(
            select(InvestigationOperationEvent)
            .where(InvestigationOperationEvent.operation_id.in_(operation_ids))
            .order_by(InvestigationOperationEvent.sequence)
        )
    ).scalars().all() if operation_ids else []
    events_by_operation: dict[int, list[InvestigationOperationEvent]] = {}
    for event in operation_events:
        events_by_operation.setdefault(event.operation_id, []).append(event)
    ai_rows = (
        await session.execute(
            select(InvestigationAiInvocation)
            .where(InvestigationAiInvocation.investigation_id == run.id, InvestigationAiInvocation.id > ai_cursor)
            .order_by(InvestigationAiInvocation.id)
            .limit(limit + 1)
        )
    ).scalars().all()
    ai_page = ai_rows[:limit]
    return {
        "operations": {
            "items": [_operation(row, events_by_operation.get(row.id, [])) for row in operation_page],
            "next_cursor": operation_page[-1].id if len(operation_rows) > limit else None,
        },
        "ai_calls": {
            "items": [{"id": row.id, "step_id": row.step_id, "purpose": row.purpose, "provider": row.provider, "model": row.model, "status": row.status, "prompt_template_version": row.prompt_template_version, "input_hash": row.input_hash, "output_hash": row.output_hash, "latency_ms": row.latency_ms, "input_tokens": row.input_tokens, "output_tokens": row.output_tokens, "total_tokens": row.total_tokens, "token_source": row.token_source, "error_code": row.error_code, "summary": row.summary, "evidence_refs": row.evidence_refs, "created_at": row.created_at} for row in ai_page],
            "next_cursor": ai_page[-1].id if len(ai_rows) > limit else None,
        },
    }


def _sse(event: str, payload: dict, event_id: int | None = None) -> str:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


@router.get("/{investigation_id}/stream")
async def stream_investigation(
    investigation_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    run = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    await assert_app_perm(session, user, run.application_id, "read")
    run_id = run.id
    try:
        header_cursor = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        header_cursor = 0
    initial_cursor = max(after, header_cursor)
    await session.close()

    async def generate():
        cursor = initial_cursor
        sent_decisions: set[int] = set()
        terminal_sent = False
        while True:
            if await request.is_disconnected():
                return
            async with AsyncSessionLocal() as stream_session:
                events = (
                    await stream_session.execute(
                        select(InvestigationOperationEvent)
                        .where(InvestigationOperationEvent.investigation_id == run_id, InvestigationOperationEvent.sequence > cursor)
                        .order_by(InvestigationOperationEvent.sequence)
                    )
                ).scalars().all()
                decisions = (
                    await stream_session.execute(
                        select(InvestigationDecision).where(InvestigationDecision.investigation_id == run_id).order_by(InvestigationDecision.ordinal)
                    )
                ).scalars().all()
                live = await stream_session.get(Investigation, run_id)
                report = await stream_session.get(InvestigationReport, run_id)
                findings = (
                    await stream_session.execute(select(InvestigationCodeFinding).where(InvestigationCodeFinding.investigation_id == run_id))
                ).scalars().all()
            for decision in decisions:
                if decision.id not in sent_decisions:
                    sent_decisions.add(decision.id)
                    yield _sse("decision.recorded", {"id": decision.id, "action": decision.action, "selected_tool": decision.selected_tool, "rationale": decision.rationale_summary})
            for event in events:
                cursor = event.sequence
                payload = {"sequence": event.sequence, "step_id": event.step_id, "operation_id": event.operation_id, "message": event.message, "detail": event.detail, "evidence_refs": event.evidence_refs, "occurred_at": event.occurred_at}
                yield _sse(f"operation.{event.kind}", payload, event.sequence)
                if event.kind == "finished":
                    yield _sse("step.updated", {"step_id": event.step_id, "sequence": event.sequence}, event.sequence)
            if live is None or live.status in {"completed", "failed"}:
                if not terminal_sent:
                    if report:
                        yield _sse("report.updated", {"result_state": report.result_state, "headline": report.headline})
                    for finding in findings:
                        yield _sse("code_finding.updated", {"id": finding.id, "status": finding.status, "path": finding.path, "start_line": finding.start_line, "end_line": finding.end_line})
                    yield _sse("investigation.finished", {"status": live.status if live else "failed", "result_state": live.result_state if live else "unavailable"}, cursor)
                    terminal_sent = True
                return
            if not events:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
