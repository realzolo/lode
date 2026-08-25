"""Canonical investigation API. No legacy analysis shape is exposed."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.ai_output import AI_OUTPUT_LANGUAGE_SETTING_KEY, normalize_ai_output_language
from lode.api.deps import assert_app_perm, permitted_app_ids, require_user
from lode.config import settings
from lode.db.models.alert import Alert
from lode.db.models.application import Application
from lode.db.models.investigation import EvidenceArtifact, EvidenceCollection, Hypothesis, Investigation, InvestigationExecutionEvent, InvestigationJob, InvestigationStage, RemediationPlan, STAGE_TYPES, SourceRevision
from lode.db.models.platform_setting import PlatformSetting
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine.evidence.secret_mask import mask_secrets

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
    """Turn an archived, redacted unified diff into read-only editor inputs."""
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


def _artifact(row: EvidenceArtifact) -> dict:
    payload = {"id": row.id, "type": row.artifact_type, "source": row.source_kind, "locator": row.locator, "content_hash": row.content_hash, "excerpt": row.redacted_excerpt, "metadata": row.metadata_, "collected_at": row.collected_at}
    if row.artifact_type == "source_file":
        payload["code"] = {"mode": "source", "language": str((row.metadata_ or {}).get("language") or "plaintext"), "content": row.redacted_excerpt, "highlight_line": (row.metadata_ or {}).get("line")}
    elif row.artifact_type == "source_diff":
        payload["code"] = _diff_view(row.redacted_excerpt)
    return payload


def _operations(events: list[InvestigationExecutionEvent]) -> dict[int, list[dict]]:
    grouped: dict[int, dict[str, dict]] = {}
    for event in events:
        stage = grouped.setdefault(event.stage_id, {})
        operation = stage.setdefault(event.operation_id, {"id": event.operation_id, "type": event.event_type, "status": "running", "collection_id": event.collection_id, "started_at": None, "finished_at": None, "detail": {}, "artifact_refs": [], "sequence": event.sequence})
        operation["detail"] = {**operation["detail"], **(event.detail or {})}
        operation["artifact_refs"] = list(dict.fromkeys([*operation["artifact_refs"], *(event.artifact_refs or [])]))
        if event.phase == "started":
            operation["started_at"] = event.occurred_at
        else:
            operation["status"] = event.phase
            operation["finished_at"] = event.occurred_at
            operation["sequence"] = event.sequence
    return {stage_id: sorted(rows.values(), key=lambda row: row["sequence"]) for stage_id, rows in grouped.items()}


@router.get("")
async def list_investigations(user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> list[dict]:
    user = await session.get(User, user_id)
    app_ids = await permitted_app_ids(session, user_id, user.role)
    query = select(Investigation, Application.name, Alert.title, Alert.level).join(Application, Application.id == Investigation.application_id).outerjoin(Alert, Alert.id == Investigation.alert_id).order_by(Investigation.created_at.desc())
    if app_ids is not None:
        query = query.where(Investigation.application_id.in_(app_ids))
    return [{"id": run.public_id, "application_id": run.application_id, "application_name": app_name, "title": title or "", "level": level or "WARNING", "status": run.status, "confidence": float(run.confidence) if run.confidence is not None else None, "conclusion": run.conclusion, "created_at": run.created_at} for run, app_name, title, level in (await session.execute(query)).all()]


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str, user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> dict:
    run = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    await assert_app_perm(session, user, run.application_id, "read")
    app = await session.get(Application, run.application_id)
    alert = await session.get(Alert, run.alert_id)
    stages = (await session.execute(select(InvestigationStage).where(InvestigationStage.investigation_id == run.id).order_by(InvestigationStage.order_index))).scalars().all()
    collections = (await session.execute(select(EvidenceCollection).where(EvidenceCollection.investigation_id == run.id).order_by(EvidenceCollection.id))).scalars().all()
    events = (await session.execute(select(InvestigationExecutionEvent).where(InvestigationExecutionEvent.investigation_id == run.id).order_by(InvestigationExecutionEvent.sequence))).scalars().all()
    artifacts = (await session.execute(select(EvidenceArtifact).where(EvidenceArtifact.investigation_id == run.id).order_by(EvidenceArtifact.id))).scalars().all()
    revisions = (await session.execute(select(SourceRevision).where(SourceRevision.investigation_id == run.id).order_by(SourceRevision.id))).scalars().all()
    hypotheses = (await session.execute(select(Hypothesis).where(Hypothesis.investigation_id == run.id).order_by(Hypothesis.rank))).scalars().all()
    remediation = (await session.execute(select(RemediationPlan).where(RemediationPlan.investigation_id == run.id))).scalars().first()
    job = (await session.execute(select(InvestigationJob).where(InvestigationJob.investigation_id == run.id).order_by(InvestigationJob.id.desc()).limit(1))).scalars().first()
    grouped = {stage.id: [] for stage in stages}
    for collection in collections:
        grouped.setdefault(collection.stage_id, []).append({"id": collection.id, "connector": collection.connector_kind, "status": collection.status, "selector": collection.selector, "config_hash": collection.config_hash, "artifact_count": collection.artifact_count, "failure_code": collection.failure_code, "failure_detail": collection.failure_detail, "started_at": collection.started_at, "finished_at": collection.finished_at})
    operations = _operations(events)
    return {
        "id": run.public_id,
        "application": {"id": run.application_id, "name": app.name if app else ""},
        "alert": {"title": alert.title, "level": alert.level, "topic": alert.topic, "error_message": alert.error_message} if alert else None,
        "status": run.status,
        "output_language": run.output_language,
        "scope": {"service": run.service_name, "environment": run.environment, "trace_id": run.trace_id, "deployment_sha": run.deployment_sha, "window_started_at": run.window_started_at, "window_finished_at": run.window_finished_at, **(run.scope or {})},
        "conclusion": run.conclusion,
        "confidence": float(run.confidence) if run.confidence is not None else None,
        "stages": [{"name": row.stage_type, "status": row.status, "order": row.order_index, "input": row.input, "output": row.output, "failure_code": row.failure_code, "failure_detail": row.failure_detail, "started_at": row.started_at, "finished_at": row.finished_at, "collections": grouped.get(row.id, []), "operations": operations.get(row.id, [])} for row in stages],
        "source_revisions": [{"role": row.role, "requested_ref": row.requested_ref, "resolved_sha": row.resolved_sha, "origin_url": row.origin_url, "status": row.status, "failure_detail": row.failure_detail} for row in revisions],
        "evidence": [_artifact(row) for row in artifacts],
        "hypotheses": [{"rank": row.rank, "status": row.status, "text": row.text, "confidence": float(row.confidence), "evidence_refs": row.evidence_refs} for row in hypotheses],
        "remediation": None if remediation is None else {"summary": remediation.summary, "risk_level": remediation.risk_level, "evidence_refs": remediation.evidence_refs, "preconditions": remediation.preconditions, "steps": remediation.steps, "verification": remediation.verification, "rollback": remediation.rollback, "agent_prompt": remediation.agent_prompt},
        "job": None if job is None else {"status": job.status, "attempt": job.attempt, "max_attempts": job.max_attempts, "last_error_code": job.last_error_code, "last_error_detail": job.last_error_detail},
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
    }


@router.post("/{investigation_id}/reanalyze", status_code=201)
async def reinvestigate(investigation_id: str, user_id: int = Depends(require_user), session: AsyncSession = Depends(get_session)) -> dict:
    prior = await _run(session, investigation_id)
    user = await session.get(User, user_id)
    await assert_app_perm(session, user, prior.application_id, "analyze")
    active = (await session.execute(select(Investigation).where(Investigation.incident_id == prior.incident_id).where(Investigation.status.in_(["queued", "running"])).limit(1))).scalars().first()
    if active is not None:
        raise HTTPException(status_code=409, detail="an investigation is already active for this incident")
    setting = await session.get(PlatformSetting, AI_OUTPUT_LANGUAGE_SETTING_KEY)
    language = normalize_ai_output_language(setting.value if setting is not None else None)
    now = datetime.now(UTC)
    run = Investigation(application_id=prior.application_id, alert_id=prior.alert_id, incident_id=prior.incident_id, trigger_signature=prior.trigger_signature, status="queued", output_language=language, service_name=prior.service_name, environment=prior.environment, trace_id=prior.trace_id, deployment_sha=prior.deployment_sha, window_started_at=prior.window_started_at, window_finished_at=prior.window_finished_at, scope={"trigger": "manual_reanalysis", "requested_by": user_id})
    session.add(run)
    await session.flush()
    session.add_all(InvestigationStage(investigation_id=run.id, stage_type=name, status="queued", order_index=index, input={}, output={}) for index, name in enumerate(STAGE_TYPES))
    session.add(InvestigationJob(incident_id=run.incident_id, investigation_id=run.id, status="queued", max_attempts=settings.job_max_attempts))
    alert = await session.get(Alert, run.alert_id)
    if alert is not None:
        raw = json.dumps({"title": alert.title, "level": alert.level, "error_message": alert.error_message}, ensure_ascii=False)
        excerpt = mask_secrets(raw)[0]
        session.add(EvidenceArtifact(investigation_id=run.id, collection_id=None, artifact_type="alert", source_kind="alert", source_id=alert.id, locator=f"alert://{alert.id}", content_hash=hashlib.sha256(raw.encode()).hexdigest(), redacted_excerpt=excerpt, metadata_={"time_scope": "incident_input"}))
    await session.commit()
    return {"id": run.public_id, "status": "queued"}
