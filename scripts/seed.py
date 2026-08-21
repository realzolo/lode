"""Seed the live database with a demo application, alerts, analyses, and memory.

Run from the project root:

    .venv/bin/python3 scripts/seed.py

The script is idempotent: if the demo application already exists it exits
early so re-running it never duplicates data. The analysis workflow is driven
by the real engine (``run_analysis``), so seeding also exercises the agentic
loop and proves the end-to-end path against the live PostgreSQL instance.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from incident_trace.consumer.dedupe import compute_dedupe_key
from incident_trace.db.models.alert import Alert
from incident_trace.db.models.analysis import Analysis
from incident_trace.db.models.application import (
    Application,
    ApplicationKafka,
    ApplicationRepo,
    DbSource,
    PresetPrompt,
)
from incident_trace.db.models.git import GitCredential, GitRepo
from incident_trace.db.models.memory import Memory
from incident_trace.db.models.user import User
from incident_trace.db.session import AsyncSessionLocal
from incident_trace.engine import run_analysis
from incident_trace.security import hash_password

# Dev-only password assigned to the seed admin so the UI can log in.
SEED_ADMIN_PASSWORD = "incident-trace"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("incident_trace.seed")

APP_NAME = "checkout-service"
TOPIC = "alert.checkout"


async def _seed_base(session) -> int:
    existing = (
        await session.execute(select(Application).where(Application.name == APP_NAME))
    ).scalars().first()
    if existing is not None:
        logger.info("demo application '%s' already exists (id=%s); skipping", APP_NAME, existing.id)
        return existing.id

    user = User(email="admin@incident-trace.local", name="Seed Admin", role="admin", status="active")
    user.password_hash = hash_password(SEED_ADMIN_PASSWORD)
    session.add(user)
    await session.flush()
    logger.info("seed admin credentials -> email=%s password=%s", user.email, SEED_ADMIN_PASSWORD)

    app = Application(name=APP_NAME, created_by=user.id)
    session.add(app)
    await session.flush()

    session.add(ApplicationKafka(application_id=app.id, topic=TOPIC))

    cred = GitCredential(auth_type="ssh", username="git", secret_ref="vault://git/readonly", note="global RO account")
    session.add(cred)
    await session.flush()

    checkout_repo = GitRepo(name="acme/checkout-service", repo_url="git@github.com:acme/checkout-service.git",
                            default_branch="main", credential_id=cred.id)
    payments_repo = GitRepo(name="acme/payments-core", repo_url="git@github.com:acme/payments-core.git",
                            default_branch="main", credential_id=cred.id)
    session.add_all([checkout_repo, payments_repo])
    await session.flush()

    session.add_all([
        ApplicationRepo(application_id=app.id, repo_id=checkout_repo.id,
                        description="Payment & order checkout API (entrypoint for /checkout)"),
        ApplicationRepo(application_id=app.id, repo_id=payments_repo.id,
                        description="Shared payment primitives: pools, retry, provider adapters"),
    ])

    session.add(PresetPrompt(
        application_id=app.id, type="deploy",
        content=(
            "Deploys run Mon-Fri at 14:00 UTC. The 2026-08-21 13:55 UTC deploy bumped the "
            "DB connection pool from 20 to 40 but misconfigured replica DNS, introducing "
            "8s+ query waits on the orders replica."
        ),
    ))

    session.add(DbSource(
        application_id=app.id, name="orders-replica",
        conn_secret_ref="vault://db/orders-replica-ro",
        allowed_tables=["orders", "transactions", "payments"],
    ))

    await session.commit()
    logger.info("created application id=%s topic=%s", app.id, TOPIC)
    return app.id


async def _make_alert(session, application_id: int, event_type: str, title: str,
                      level: str, error_message: str, fields: dict) -> int:
    dedupe_key = compute_dedupe_key(event_type=event_type, title=title, fields=fields)
    alert = Alert(
        dedupe_key=dedupe_key, application_id=application_id, topic=TOPIC,
        title=title, level=level, env="production",
        error_message=error_message, fields=fields,
        raw_payload={"event_type": event_type, "title": title, "fields": fields},
    )
    session.add(alert)
    await session.flush()
    return alert.id, dedupe_key


async def main() -> None:
    app_id = await _seed_base(AsyncSessionLocal())

    async with AsyncSessionLocal() as session:
        # Alert 1 — a checkout timeout (the canonical demo incident).
        alert1_id, key1 = await _make_alert(
            session, app_id, "checkout_error", "Checkout timeout during payment", "CRITICAL",
            "psycopg2.pool.PoolError: connection pool exhausted (used=40 wait=8400ms)",
            {"orderId": "ORD-99213", "provider": "stripe", "status": "failed",
             "message": "timed out after 8000ms"},
        )
        # Alert 2 — a gateway warning.
        alert2_id, key2 = await _make_alert(
            session, app_id, "payment_warn", "Elevated 5xx on gateway", "WARNING",
            "upstream timeout > 2000ms on /gateway/authorize",
            {"channelId": "ch_gw", "provider": "adyen", "status": "degraded"},
        )

        # Seed a pre-existing shared memory so analysis 1's memory step matches.
        mem = Memory(
            application_id=app_id, trigger_signature=key1,
            content=(
                "Checkout connection-pool exhaustion correlates with deploys that change "
                "replica DNS (first seen 2026-08-14). Roll back the replica DNS change and "
                "restore pool size to 20 while investigating."
            ),
            is_valid=True, source_analysis_id=None,
        )
        session.add(mem)

        analysis1 = Analysis(dedupe_key=key1, application_id=app_id, alert_id=alert1_id, status="pending")
        analysis2 = Analysis(dedupe_key=key2, application_id=app_id, alert_id=alert2_id, status="pending")
        session.add_all([analysis1, analysis2])
        await session.commit()
        a1_id, a2_id = analysis1.id, analysis2.id
        logger.info("created alerts + analyses (a1=%s key=%s, a2=%s key=%s)", a1_id, key1, a2_id, key2)

    # Drive both through the real engine (heuristic path — no LLM key needed).
    for aid in (a1_id, a2_id):
        async with AsyncSessionLocal() as session:
            await run_analysis(aid, session)
        logger.info("engine finished for analysis %s", aid)

    logger.info("seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
