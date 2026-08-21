"""Integration tests for the Phase 1 analysis engine.

Exercises the agentic workflow end-to-end against the live database: the engine
runs in its deterministic heuristic mode (no LLM configured) and must produce a
completed analysis. Critically, re-running analysis must *upsert* shared memory
by trigger signature — never create duplicate rows.

All rows created here are cleaned up afterwards so the suite leaves no residue.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import delete, select

from incident_trace.consumer.dedupe import compute_dedupe_key
from incident_trace.db.models.alert import Alert
from incident_trace.db.models.analysis import Analysis, AnalysisStep
from incident_trace.db.models.application import Application, PresetPrompt
from incident_trace.db.models.memory import Memory
from incident_trace.db.session import AsyncSessionLocal
from incident_trace.engine import run_analysis


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
            PresetPrompt(
                application_id=application_id,
                type="deploy",
                content="Deploys happen nightly; the last deploy bumped pool size to 40.",
            )
        )

        alert = Alert(
            dedupe_key=dedupe_key,
            application_id=application_id,
            topic=topic,
            title="Engine test failure",
            level="CRITICAL",
            env="production",
            error_message="TimeoutException: connection pool exhausted",
            fields={"orderId": key},
            raw_payload={"event_type": "engine_test_error", "title": "Engine test failure"},
        )
        session.add(alert)
        await session.flush()
        alert_id = alert.id

        analysis = Analysis(
            dedupe_key=dedupe_key,
            application_id=application_id,
            alert_id=alert_id,
            status="pending",
        )
        session.add(analysis)
        await session.commit()
        await session.refresh(analysis)
        analysis_id = analysis.id

    yield {
        "application_id": application_id,
        "alert_id": alert_id,
        "analysis_id": analysis_id,
        "dedupe_key": dedupe_key,
    }

    # Clean up everything this test created, in FK-safe order.
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AnalysisStep).where(AnalysisStep.analysis_id == analysis_id)
        )
        await session.execute(
            delete(Memory).where(
                Memory.application_id == application_id,
                Memory.trigger_signature == dedupe_key,
            )
        )
        await session.execute(delete(Analysis).where(Analysis.id == analysis_id))
        await session.execute(delete(Alert).where(Alert.id == alert_id))
        await session.execute(
            delete(PresetPrompt).where(PresetPrompt.application_id == application_id)
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
        assert refreshed.status == "completed"
        assert refreshed.conclusion
        assert refreshed.confidence is not None
        assert refreshed.confidence >= 0.7

        # The workflow emits exactly the six expected nodes.
        steps = (
            await session.execute(
                select(AnalysisStep).where(AnalysisStep.analysis_id == scenario["analysis_id"])
            )
        ).scalars().all()
        node_types = {s.node_type for s in steps}
        assert node_types == {
            "receive",
            "git_sync",
            "context",
            "ai_analysis",
            "memory",
            "conclusion",
        }

        # A new memory row is recorded (engine was confident, no prior match).
        memories = (
            await session.execute(
                select(Memory).where(
                    Memory.application_id == scenario["application_id"],
                    Memory.trigger_signature == scenario["dedupe_key"],
                )
            )
        ).scalars().all()
        assert len(memories) == 1
        assert memories[0].is_valid is True


async def test_reanalysis_upserts_memory_no_duplicate(scenario):
    async with AsyncSessionLocal() as session:
        await run_analysis(scenario["analysis_id"], session)
        # Re-run the same analysis.
        await run_analysis(scenario["analysis_id"], session)

        memories = (
            await session.execute(
                select(Memory).where(
                    Memory.application_id == scenario["application_id"],
                    Memory.trigger_signature == scenario["dedupe_key"],
                )
            )
        ).scalars().all()
        # Exactly one memory row: re-analysis upserted, not duplicated.
        assert len(memories) == 1
