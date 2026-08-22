"""Analysis routes: list, detail, human hints, and re-analysis."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import assert_app_perm, permitted_app_ids, require_user
from lode.api.schemas import (
    AddHintIn,
    AnalysisDetailOut,
    AnalysisHintOut,
    AnalysisListOut,
    AnalysisStepOut,
    AlertSummary,
    ReanalyzeOut,
)
from lode.db.models.alert import Alert
from lode.db.models.analysis import Analysis, AnalysisHint, AnalysisStep
from lode.db.models.application import Application
from lode.db.models.memory import Memory
from lode.db.models.permission import UserApplicationPerm
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine import run_analysis

logger = logging.getLogger("lode.api.analyses")

router = APIRouter(prefix="/analyses", tags=["analyses"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def _latest_analysis(session: AsyncSession, dedupe_key: str) -> Analysis:
    result = await session.execute(
        select(Analysis)
        .where(Analysis.dedupe_key == dedupe_key)
        .order_by(Analysis.id.desc())
    )
    analysis = result.scalars().first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return analysis


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


@router.get("/{dedupe_key}", response_model=AnalysisDetailOut)
async def get_analysis(
    dedupe_key: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> AnalysisDetailOut:
    analysis = await _latest_analysis(session, dedupe_key)

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

    hints = (
        await session.execute(
            select(AnalysisHint)
            .where(AnalysisHint.analysis_id == analysis.id)
            .order_by(AnalysisHint.created_at)
        )
    ).scalars().all()

    matched = (
        await session.execute(
            select(Memory)
            .where(Memory.application_id == analysis.application_id)
            .where(Memory.trigger_signature == analysis.dedupe_key)
            .where(Memory.is_valid.is_(True))
            .order_by(Memory.updated_at.desc())
        )
    ).scalars().first()

    if user.role == "admin":
        my_perm = "admin"
    else:
        perm_row = await session.get(
            UserApplicationPerm, (user_id, analysis.application_id)
        )
        my_perm = perm_row.perm if perm_row is not None else None

    return AnalysisDetailOut(
        dedupe_key=analysis.dedupe_key,
        application_id=analysis.application_id,
        application_name=app_name,
        status=analysis.status,
        confidence=float(analysis.confidence) if analysis.confidence is not None else None,
        conclusion=analysis.conclusion,
        evidence=analysis.evidence,
        alert=(
            AlertSummary(
                title=alert.title,
                level=alert.level,
                env=alert.env,
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
            )
            for s in steps
        ],
        hints=[
            AnalysisHintOut(id=h.id, author=h.author, content=h.content, created_at=h.created_at)
            for h in hints
        ],
        matched_memory=matched.content if matched is not None else None,
        started_at=analysis.started_at,
        finished_at=analysis.finished_at,
        updated_at=analysis.updated_at,
        my_perm=my_perm,
    )


@router.post("/{dedupe_key}/hints", response_model=AnalysisHintOut)
async def add_hint(
    dedupe_key: str,
    payload: AddHintIn,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> AnalysisHintOut:
    analysis = await _latest_analysis(session, dedupe_key)
    user = await session.get(User, user_id)
    # Adding a human hint is annotation-level access — read or above suffices.
    await assert_app_perm(session, user, analysis.application_id, "read")
    hint = AnalysisHint(
        analysis_id=analysis.id,
        author=payload.author or "anonymous",
        content=payload.content,
    )
    session.add(hint)
    await session.commit()
    await session.refresh(hint)
    return AnalysisHintOut(
        id=hint.id, author=hint.author, content=hint.content, created_at=hint.created_at
    )


async def _run_in_background(analysis_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await run_analysis(analysis_id, session)


@router.post("/{dedupe_key}/reanalyze", response_model=ReanalyzeOut)
async def reanalyze(
    dedupe_key: str,
    user_id: int = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ReanalyzeOut:
    analysis = await _latest_analysis(session, dedupe_key)
    user = await session.get(User, user_id)
    # Re-running the pipeline is an action that consumes compute, so it
    # requires at least the "analyze" tier (not read-only viewers).
    await assert_app_perm(session, user, analysis.application_id, "analyze")
    if analysis.status == "running":
        raise HTTPException(status_code=409, detail="analysis is already running")
    analysis.status = "running"
    analysis.started_at = None
    analysis.finished_at = None
    analysis.conclusion = None
    analysis.confidence = None
    analysis.evidence = None
    await session.commit()

    asyncio.create_task(_run_in_background(analysis.id))
    return ReanalyzeOut(
        dedupe_key=analysis.dedupe_key,
        status="running",
        message="Re-analysis started. Poll GET /analyses/{dedupe_key} for progress.",
    )
