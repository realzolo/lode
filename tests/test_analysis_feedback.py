"""Feedback authorization and idempotency for analysis outcomes."""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from lode.api.main import app
from lode.db.models.alert import Alert
from lode.db.models.analysis import Analysis, AnalysisFeedback, AnalysisStep
from lode.db.models.application import Application
from lode.db.models.intake import AnalysisJob, Incident
from lode.db.models.permission import UserApplicationPerm
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def feedback_scenario():
    suffix = uuid.uuid4().hex
    email = f"feedback-{suffix}@lode.local"
    password = "feedback-pass-1"
    async with AsyncSessionLocal() as session:
        user = User(email=email, name="Feedback User", role="user", status="active")
        user.password_hash = hash_password(password)
        session.add(user)
        application = Application(name=f"feedback-app-{suffix}")
        session.add(application)
        await session.flush()
        session.add(UserApplicationPerm(user_id=user.id, application_id=application.id, perm="read"))
        alert = Alert(
            dedupe_key=f"feedback-key-{suffix}", application_id=application.id,
            topic=f"feedback.{suffix}", title="Feedback test", level="WARNING",
            raw_payload={"event_type": "feedback_test"},
        )
        session.add(alert)
        await session.flush()
        incident = Incident(
            public_id=str(uuid.uuid4()), application_id=application.id,
            dedupe_key=alert.dedupe_key, state="open", first_alert_id=alert.id,
            latest_alert_id=alert.id, alert_count=1,
        )
        session.add(incident)
        await session.flush()
        analysis = Analysis(
            public_id=uuid.uuid4().hex, dedupe_key=alert.dedupe_key,
            application_id=application.id, alert_id=alert.id, incident_id=incident.id,
            status="completed",
        )
        session.add(analysis)
        await session.flush()
        session.add(AnalysisStep(
            analysis_id=analysis.id, node_type="receive", status="completed", order_index=0,
        ))
        session.add(AnalysisJob(
            public_id=str(uuid.uuid4()), incident_id=incident.id, analysis_id=analysis.id,
            status="succeeded",
        ))
        await session.commit()
        result = {"email": email, "password": password, "analysis_id": analysis.public_id, "user_id": user.id, "application_id": application.id}

    yield result

    async with AsyncSessionLocal() as session:
        await session.execute(delete(AnalysisFeedback).where(AnalysisFeedback.actor_id == result["user_id"]))
        application = await session.get(Application, result["application_id"])
        if application is not None:
            await session.delete(application)
        user = await session.get(User, result["user_id"])
        if user is not None:
            await session.delete(user)
        await session.commit()


async def test_feedback_requires_analyze_permission_and_upserts(feedback_scenario) -> None:
    async with _client() as client:
        login = await client.post(
            "/auth/login",
            json={"email": feedback_scenario["email"], "password": feedback_scenario["password"]},
        )
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        denied = await client.post(
            f"/analyses/{feedback_scenario['analysis_id']}/feedback",
            headers=headers,
            json={"target": "remediation", "value": "useful"},
        )
        assert denied.status_code == 403

    async with AsyncSessionLocal() as session:
        permission = await session.get(
            UserApplicationPerm,
            (feedback_scenario["user_id"], feedback_scenario["application_id"]),
        )
        assert permission is not None
        permission.perm = "analyze"
        await session.commit()

    async with _client() as client:
        login = await client.post(
            "/auth/login",
            json={"email": feedback_scenario["email"], "password": feedback_scenario["password"]},
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        first = await client.post(
            f"/analyses/{feedback_scenario['analysis_id']}/feedback",
            headers=headers,
            json={"target": "remediation", "value": "useful"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["remediation_useful"] == 1
        changed = await client.post(
            f"/analyses/{feedback_scenario['analysis_id']}/feedback",
            headers=headers,
            json={"target": "remediation", "value": "not_useful"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["remediation_useful"] == 0
        assert changed.json()["remediation_not_useful"] == 1
