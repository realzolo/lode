"""Analysis routes: list, detail, operator guidance, and re-analysis."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.config import settings
from lode.api.audit import audit_action
from lode.api.deps import assert_app_perm, permitted_app_ids, require_user
from lode.api.schemas import (
    AddGuidanceIn,
    AnalysisDetailOut,
    EvidenceArtifactOut,
    AnalysisGuidanceOut,
    AnalysisJobOut,
    AnalysisListOut,
    AnalysisStepOut,
    AlertSummary,
    ReanalyzeOut,
)
from lode.db.models.alert import Alert
from lode.db.models.analysis import Analysis, AnalysisGuidance, AnalysisGuidanceUse, AnalysisStep
from lode.db.models.application import Application
from lode.db.models.intake import AnalysisJob, EvidenceArtifact, Incident
from lode.db.models.experience import Experience
from lode.db.models.permission import UserApplicationPerm
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal

logger = logging.getLogger("lode.api.analyses")

router = APIRouter(prefix="/analyses", tags=["analyses"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _analysis_by_public_id(session: AsyncSession, analysis_id: str) -> Analysis:
    result = await session.execute(
        select(Analysis)
        .where(Analysis.public_id == analysis_id)
    )
    analysis = result.scalars().first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return analysis


async def _incident_for_analysis(session: AsyncSession, analysis: Analysis) -> Incident:
    """Return the incident backing an analysis, creating it for legacy callers."""
    if analysis.incident_id is not None:
        incident = await session.get(Incident, analysis.incident_id)
        if incident is not None:
            return incident

    incident = (
        await session.execute(
            select(Incident)
            .where(Incident.application_id == analysis.application_id)
            .where(Incident.dedupe_key == analysis.dedupe_key)
            .limit(1)
        )
    ).scalars().first()
    if incident is None:
        incident = Incident(
            public_id=uuid.uuid4().hex,
            application_id=analysis.application_id,
            dedupe_key=analysis.dedupe_key,
            state="open",
            first_alert_id=analysis.alert_id,
            latest_alert_id=analysis.alert_id,
            alert_count=1,
        )
        session.add(incident)
        await session.flush()
    analysis.incident_id = incident.id
    return incident


def _guidance_effect(
    analysis: Analysis,
    ai_step: AnalysisStep | None,
    usage: AnalysisGuidanceUse | None,
) -> str:
    if usage is not None:
        return "applied"
    if analysis.status in {"completed", "failed", "canceled"}:
        return "needs_reanalysis"
    if ai_step is not None and ai_step.status in {"running", "completed", "failed", "skipped"}:
        return "needs_reanalysis"
    return "will_apply"


@router.get("", response_model=list[AnalysisListOut])
async def list_analyses(
    application_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[AnalysisListOut]:
    user = await session.get(User, user_id)
    app_ids = await permitted_app_ids(session, user_id, user.role)
    stmt = (
        select(
            Analysis,
            Application.name,
            Alert.title,
            Alert.level,
            Alert.received_at,
        )
        .join(Application, Application.id == Analysis.application_id)
        .outerjoin(Alert, Alert.id == Analysis.alert_id)
        .order_by(Analysis.created_at.desc())
        .limit(limit)
    )
    if application_id is not None:
        stmt = stmt.where(Analysis.application_id == application_id)
    if app_ids is not None:
        stmt = stmt.where(Analysis.application_id.in_(app_ids))

    rows = (await session.execute(stmt)).all()

    # Resolve the caller's permission per application so the UI can gate
    # actions (e.g. re-analyze). Global admins are unrestricted -> "admin".
    my_perm: dict[int, str] = {}
    if user.role != "admin":
        perm_rows = (
            await session.execute(
                select(UserApplicationPerm).where(
                    UserApplicationPerm.user_id == user_id
                )
            )
        ).scalars().all()
        my_perm = {r.application_id: r.perm for r in perm_rows}

    return [
        AnalysisListOut(
            id=a.public_id,
            dedupe_key=a.dedupe_key,
            application_id=a.application_id,
            application_name=app_name,
            title=alert_title or "",
            level=alert_level or "WARNING",
            status=a.status,
            confidence=float(a.confidence) if a.confidence is not None else None,
            conclusion=a.conclusion,
            received_at=received_at,
            updated_at=a.updated_at,
            my_perm="admin" if user.role == "admin" else my_perm.get(a.application_id),
        )
        for a, app_name, alert_title, alert_level, received_at in rows
    ]


@router.get("/{analysis_id}", response_model=AnalysisDetailOut)
async def get_analysis(
    analysis_id: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> AnalysisDetailOut:
    analysis = await _analysis_by_public_id(session, analysis_id)

    user = await session.get(User, user_id)
    await assert_app_perm(session, user, analysis.application_id, "read")

    app_name = await session.execute(
        select(Application.name).where(Application.id == analysis.application_id)
    )
    app_name = app_name.scalar_one()

    alert: Alert | None = None
    if analysis.alert_id is not None:
        alert = (
            await session.execute(select(Alert).where(Alert.id == analysis.alert_id))
        ).scalars().first()

    steps = (
        await session.execute(
            select(AnalysisStep)
            .where(AnalysisStep.analysis_id == analysis.id)
            .order_by(AnalysisStep.order_index)
        )
    ).scalars().all()
    job = (
        await session.execute(
            select(AnalysisJob)
            .where(AnalysisJob.analysis_id == analysis.id)
            .order_by(AnalysisJob.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if job is None:
        raise HTTPException(status_code=409, detail="analysis has no execution job")

    incident = await _incident_for_analysis(session, analysis)
    ai_step = next((step for step in steps if step.node_type == "ai_analysis"), None)
    guidance_rows = (
        await session.execute(
            select(AnalysisGuidance, AnalysisGuidanceUse)
            .outerjoin(
                AnalysisGuidanceUse,
                and_(
                    AnalysisGuidanceUse.guidance_id == AnalysisGuidance.id,
                    AnalysisGuidanceUse.analysis_id == analysis.id,
                ),
            )
            .where(AnalysisGuidance.incident_id == incident.id)
            .order_by(AnalysisGuidance.created_at)
        )
    ).all()

    matched = (
        await session.execute(
            select(Experience)
            .where(Experience.application_id == analysis.application_id)
            .where(Experience.trigger_signature == analysis.dedupe_key)
            .where(Experience.is_valid.is_(True))
            .order_by(Experience.updated_at.desc())
        )
    ).scalars().first()

    if user.role == "admin":
        my_perm = "admin"
    else:
        perm_row = await session.get(
            UserApplicationPerm, (user_id, analysis.application_id)
        )
        my_perm = perm_row.perm if perm_row is not None else None

    artifacts = (
        await session.execute(
            select(EvidenceArtifact)
            .where(EvidenceArtifact.analysis_id == analysis.id)
            .order_by(EvidenceArtifact.collected_at, EvidenceArtifact.id)
        )
    ).scalars().all()

    return AnalysisDetailOut(
        id=analysis.public_id,
        dedupe_key=analysis.dedupe_key,
        application_id=analysis.application_id,
        application_name=app_name,
        status=analysis.status,
        confidence=float(analysis.confidence) if analysis.confidence is not None else None,
        conclusion=analysis.conclusion,
        evidence=analysis.evidence,
        evidence_artifacts=[
            EvidenceArtifactOut(
                id=artifact.id,
                artifact_type=artifact.artifact_type,
                source_kind=artifact.source_kind,
                locator=artifact.locator,
                content_hash=artifact.content_hash,
                redacted_excerpt=artifact.redacted_excerpt,
                metadata=artifact.metadata_,
                collected_at=artifact.collected_at,
            )
            for artifact in artifacts
        ],
        alert=(
            AlertSummary(
                title=alert.title,
                level=alert.level,
                topic=alert.topic,
                error_message=alert.error_message,
                fields=alert.fields or {},
            )
            if alert is not None
            else None
        ),
        steps=[
            AnalysisStepOut(
                node_type=s.node_type,
                status=s.status,
                order_index=s.order_index,
                detail=(s.output or {}).get("detail") if s.output else None,
                summary=(s.output or {}).get("summary") if s.output else None,
                started_at=s.started_at,
                finished_at=s.finished_at,
            )
            for s in steps
        ],
        job=AnalysisJobOut(
            id=str(job.public_id),
            status=job.status,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            available_at=job.available_at,
            last_error_code=job.last_error_code,
            last_error_detail=job.last_error_detail,
        ),
        guidances=[
            AnalysisGuidanceOut(
                id=guidance.id,
                author=guidance.author,
                content=guidance.content,
                created_at=guidance.created_at,
                effect=_guidance_effect(analysis, ai_step, usage),
                applied_at=usage.applied_at if usage is not None else None,
            )
            for guidance, usage in guidance_rows
        ],
        follow_up_status="requested" if incident.reanalysis_requested_at is not None else "none",
        matched_experience=matched.content if matched is not None else None,
        started_at=analysis.started_at,
        finished_at=analysis.finished_at,
        updated_at=analysis.updated_at,
        my_perm=my_perm,
    )


@router.post("/{analysis_id}/guidances", response_model=AnalysisGuidanceOut)
async def add_guidance(
    analysis_id: str,
    payload: AddGuidanceIn,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> AnalysisGuidanceOut:
    analysis = await _analysis_by_public_id(session, analysis_id)
    user = await session.get(User, user_id)
    # Guidance is collaborative context. It does not itself consume compute, so
    # read access is enough; the separate re-analysis action requires analyze.
    await assert_app_perm(session, user, analysis.application_id, "read")
    incident = await _incident_for_analysis(session, analysis)
    guidance = AnalysisGuidance(
        incident_id=incident.id,
        source_analysis_id=analysis.id,
        author_id=user.id,
        author=user.name or user.email,
        content=payload.content,
    )
    session.add(guidance)
    await session.commit()
    await session.refresh(guidance)
    ai_step = (
        await session.execute(
            select(AnalysisStep)
            .where(AnalysisStep.analysis_id == analysis.id)
            .where(AnalysisStep.node_type == "ai_analysis")
            .limit(1)
        )
    ).scalars().first()
    return AnalysisGuidanceOut(
        id=guidance.id,
        author=guidance.author,
        content=guidance.content,
        created_at=guidance.created_at,
        effect=_guidance_effect(analysis, ai_step, None),
    )


@router.post("/{analysis_id}/reanalyze", response_model=ReanalyzeOut)
async def reanalyze(
    analysis_id: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ReanalyzeOut:
    analysis = await _analysis_by_public_id(session, analysis_id)
    user = await session.get(User, user_id)
    # Re-running the pipeline is an action that consumes compute, so it
    # requires at least the "analyze" tier (not read-only viewers).
    await assert_app_perm(session, user, analysis.application_id, "analyze")

    incident = await _incident_for_analysis(session, analysis)

    # A confirmed late guidance must never start parallel work. Record a single
    # successor request and let the worker create it after the active job ends.
    active = (
        await session.execute(
            select(Analysis)
            .where(Analysis.application_id == analysis.application_id)
            .where(Analysis.dedupe_key == analysis.dedupe_key)
            .where(Analysis.status.in_(["pending", "running"]))
            .limit(1)
        )
    ).scalars().first()
    if active is not None:
        incident.reanalysis_requested_at = datetime.now(UTC)
        incident.reanalysis_requested_by = user_id
        await session.commit()
        await audit_action(
            action="analysis.reanalysis_requested",
            actor_id=user_id,
            target_type="application",
            target_id=str(analysis.application_id),
            application_id=analysis.application_id,
            result="ok",
            detail={"dedupe_key": analysis.dedupe_key, "after_analysis_id": active.public_id},
        )
        return ReanalyzeOut(
            analysis_id=active.public_id,
            status="scheduled_after_active",
            message="Re-analysis will start after the active analysis finishes.",
        )

    # Create a fresh analysis run + queued job; the worker executes it.
    new_analysis = Analysis(
        public_id=uuid.uuid4().hex,
        dedupe_key=analysis.dedupe_key,
        application_id=analysis.application_id,
        alert_id=analysis.alert_id,
        incident_id=incident.id,
        status="pending",
        engine_version=None,
    )
    session.add(new_analysis)
    await session.flush()
    received_at = datetime.now(UTC)
    session.add(
        AnalysisStep(
            analysis_id=new_analysis.id,
            node_type="receive",
            status="completed",
            order_index=0,
            input={"source_analysis_id": analysis.id},
            output={
                "summary": "Alert selected for re-analysis",
                "detail": "Re-analysis uses the alert received by the prior run.",
            },
            started_at=received_at,
            finished_at=received_at,
        )
    )
    job = AnalysisJob(
        public_id=uuid.uuid4().hex,
        incident_id=incident.id,
        analysis_id=new_analysis.id,
        trigger="manual_reanalyze",
        status="queued",
        requested_by=user_id,
        max_attempts=settings.job_max_attempts,
    )
    session.add(job)
    await session.commit()
    await audit_action(
        action="analysis.create",
        actor_id=user_id,
        target_type="application",
        target_id=str(analysis.application_id),
        application_id=analysis.application_id,
        result="ok",
        detail={"dedupe_key": analysis.dedupe_key, "analysis_id": new_analysis.public_id, "job_id": str(job.public_id)},
    )
    return ReanalyzeOut(
        analysis_id=new_analysis.public_id,
        job_id=str(job.public_id),
        status="queued",
        message="Re-analysis queued. Poll GET /analyses/{analysis_id} for progress.",
    )
