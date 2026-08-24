"""Integration tests for the Phase 1 analysis engine.

Exercises the agentic workflow end-to-end against the live database: the engine
runs in its deterministic heuristic mode (no LLM configured) and must produce a
completed analysis. Critically, re-running analysis must *upsert* shared experience
by trigger signature — never create duplicate rows.

All rows created here are cleaned up afterwards so the suite leaves no residue.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import delete, select

from lode.consumer.dedupe import compute_dedupe_key
from lode.db.models.alert import Alert
from lode.db.models.analysis import (
    Analysis,
    AnalysisGuidance,
    AnalysisGuidanceUse,
    AnalysisRecommendation,
    AnalysisStep,
)
from lode.db.models.application import Application, ApplicationDescription
from lode.db.models.experience import Experience
from lode.db.models.intake import AnalysisJob, Incident
from lode.db.session import AsyncSessionLocal
from lode.engine import run_analysis
from lode.worker.main import _enqueue_deferred_reanalysis


@pytest_asyncio.fixture
async def scenario():
    key = uuid.uuid4().hex
    dedupe_key = compute_dedupe_key(
        event_type="engine_test_error", title="Engine test failure", fields={"orderId": key}
    )
    app_name = f"engine-test-{key}"
    topic = f"alert.engine_test.{key}"
    async with AsyncSessionLocal() as session:
        application = Application(name=app_name)
        session.add(application)
        await session.flush()
        application_id = application.id

        session.add(
            ApplicationDescription(
                application_id=application_id,
                description_type="deploy",
                content="Deploys happen nightly; the last deploy bumped pool size to 40.",
            )
        )

        alert = Alert(
            dedupe_key=dedupe_key,
            application_id=application_id,
            topic=topic,
            title="Engine test failure",
            level="CRITICAL",
            error_message="TimeoutException: connection pool exhausted",
            fields={"orderId": key},
            raw_payload={"event_type": "engine_test_error", "title": "Engine test failure"},
        )
        session.add(alert)
        await session.flush()
        alert_id = alert.id

        incident = Incident(
            public_id=str(uuid.uuid4()),
            application_id=application_id,
            dedupe_key=dedupe_key,
            state="open",
            first_alert_id=alert_id,
            latest_alert_id=alert_id,
            alert_count=1,
        )
        session.add(incident)
        await session.flush()

        analysis = Analysis(
            dedupe_key=dedupe_key,
            application_id=application_id,
            alert_id=alert_id,
            incident_id=incident.id,
            status="pending",
        )
        session.add(analysis)
        await session.flush()
        session.add(
            AnalysisStep(
                analysis_id=analysis.id,
                node_type="receive",
                status="completed",
                order_index=0,
                input={"topic": topic},
                output={"summary": "Alert received", "detail": f"Routed via topic {topic}"},
                started_at=alert.received_at,
                finished_at=alert.received_at,
            )
        )
        session.add(
            AnalysisGuidance(
                incident_id=incident.id,
                source_analysis_id=analysis.id,
                author="Engine Test",
                content="Check whether the connection pool limit changed in the last deploy.",
            )
        )
        await session.commit()
        await session.refresh(analysis)
        analysis_id = analysis.id
        incident_id = incident.id

    yield {
        "application_id": application_id,
        "alert_id": alert_id,
        "analysis_id": analysis_id,
        "incident_id": incident_id,
        "dedupe_key": dedupe_key,
    }

    # Clean up everything this test created, in FK-safe order.
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AnalysisGuidanceUse).where(AnalysisGuidanceUse.analysis_id == analysis_id)
        )
        await session.execute(
            delete(AnalysisGuidance).where(AnalysisGuidance.incident_id == incident_id)
        )
        await session.execute(
            delete(AnalysisStep).where(AnalysisStep.analysis_id == analysis_id)
        )
        await session.execute(
            delete(Experience).where(
                Experience.application_id == application_id,
                Experience.trigger_signature == dedupe_key,
            )
        )
        await session.execute(delete(Analysis).where(Analysis.id == analysis_id))
        await session.execute(delete(Incident).where(Incident.id == incident_id))
        await session.execute(delete(Alert).where(Alert.id == alert_id))
        await session.execute(
            delete(ApplicationDescription).where(
                ApplicationDescription.application_id == application_id
            )
        )
        await session.execute(delete(Application).where(Application.id == application_id))
        await session.commit()


async def test_run_analysis_completes(scenario):
    async with AsyncSessionLocal() as session:
        await run_analysis(scenario["analysis_id"], session)

        refreshed = (
            await session.execute(select(Analysis).where(Analysis.id == scenario["analysis_id"]))
        ).scalars().first()
        assert refreshed is not None
        assert refreshed.status == "needs_review"
        assert refreshed.conclusion
        assert refreshed.confidence is not None
        assert refreshed.confidence <= 0.2

        # The workflow records the evidence snapshot stage before synthesis.
        steps = (
            await session.execute(
                select(AnalysisStep).where(AnalysisStep.analysis_id == scenario["analysis_id"]).order_by(AnalysisStep.order_index)
            )
        ).scalars().all()
        node_types = {s.node_type for s in steps}
        assert node_types == {
            "receive",
            "git_sync",
            "context",
            "service_snapshot",
            "ai_analysis",
            "experience",
            "conclusion",
        }
        assert all(step.started_at is not None and step.finished_at is not None for step in steps)
        assert steps[-1].node_type == "conclusion"
        assert [step.node_type for step in steps] == [
            "receive", "git_sync", "context", "service_snapshot", "experience", "ai_analysis", "conclusion",
        ]

        recommendation = (
            await session.execute(
                select(AnalysisRecommendation).where(
                    AnalysisRecommendation.analysis_id == scenario["analysis_id"]
                )
            )
        ).scalars().one()
        assert recommendation.risk_level == "high"
        assert recommendation.prompt_markdown.startswith("# Production incident investigation")

        guidance_uses = (
            await session.execute(
                select(AnalysisGuidanceUse).where(
                    AnalysisGuidanceUse.analysis_id == scenario["analysis_id"]
                )
            )
        ).scalars().all()
        assert len(guidance_uses) == 1

        # Uncited/heuristic output is deliberately not promoted into the
        # reusable experience library.
        experiences = (
            await session.execute(
                select(Experience).where(
                    Experience.application_id == scenario["application_id"],
                    Experience.trigger_signature == scenario["dedupe_key"],
                )
            )
        ).scalars().all()
        assert experiences == []


async def test_reanalysis_upserts_experience_no_duplicate(scenario):
    async with AsyncSessionLocal() as session:
        await run_analysis(scenario["analysis_id"], session)
        # Re-run the same analysis.
        await run_analysis(scenario["analysis_id"], session)

        experiences = (
            await session.execute(
                select(Experience).where(
                    Experience.application_id == scenario["application_id"],
                    Experience.trigger_signature == scenario["dedupe_key"],
                )
            )
        ).scalars().all()
        assert experiences == []


async def test_confirmed_follow_up_creates_one_successor(scenario):
    async with AsyncSessionLocal() as session:
        analysis = await session.get(Analysis, scenario["analysis_id"])
        incident = await session.get(Incident, scenario["incident_id"])
        assert analysis is not None and incident is not None
        analysis.status = "completed"
        incident.reanalysis_requested_at = datetime.now(UTC)
        assert await _enqueue_deferred_reanalysis(session, analysis) is True
        assert await _enqueue_deferred_reanalysis(session, analysis) is False
        await session.commit()

        successors = (
            await session.execute(
                select(AnalysisJob).where(AnalysisJob.incident_id == incident.id)
            )
        ).scalars().all()
        assert len(successors) == 1
        assert successors[0].trigger == "guidance_reanalyze"
        steps = (
            await session.execute(
                select(AnalysisStep).where(AnalysisStep.analysis_id == successors[0].analysis_id)
            )
        ).scalars().all()
        assert [(step.node_type, step.status) for step in steps] == [("receive", "completed")]
