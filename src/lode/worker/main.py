"""Durable worker for canonical investigation jobs."""

from __future__ import annotations

import asyncio
import logging
import platform
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lode.config import settings
from lode.db.models.application import Application
from lode.db.models.intake import Incident
from lode.db.models.investigation import Investigation, InvestigationJob
from lode.db.session import AsyncSessionLocal
from lode.engine.investigation_runner import run_investigation
from lode.metrics import ANALYSES, ENGINE_IN_FLIGHT

logger = logging.getLogger("lode.worker")
WORKER_ID = f"{platform.node()}:{uuid.uuid4().hex[:8]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(token in str(exc).lower() for token in ("timeout", "connection", "502", "503", "reset"))


async def reclaim_expired_leases(session: AsyncSession) -> int:
    result = await session.execute(
        update(InvestigationJob)
        .where(InvestigationJob.status == "running")
        .where(InvestigationJob.lease_expires_at < _now())
        .values(status="queued", lease_owner=None, lease_expires_at=None)
    )
    await session.execute(update(Investigation).where(Investigation.status == "running").values(status="queued"))
    if result.rowcount:
        await session.commit()
    return result.rowcount or 0


def _claimable_jobs_query():
    return (
        select(InvestigationJob)
        .join(Incident, Incident.id == InvestigationJob.incident_id)
        .join(Application, Application.id == Incident.application_id)
        .where(InvestigationJob.status.in_(["queued", "retry_wait"]))
        .where(InvestigationJob.available_at <= _now())
        .where(Application.ingestion_state == "active")
        .order_by(InvestigationJob.priority.desc(), InvestigationJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def claim_job(session: AsyncSession) -> InvestigationJob | None:
    job = (await session.execute(_claimable_jobs_query())).scalars().first()
    if job is None:
        return None
    job.status = "running"
    job.attempt += 1
    job.lease_owner = WORKER_ID
    job.lease_expires_at = _now() + timedelta(seconds=settings.worker_lease_ttl_seconds)
    job.started_at = job.started_at or _now()
    await session.commit()
    return job


async def _heartbeat(job_id: int) -> None:
    interval = max(1.0, settings.worker_lease_ttl_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(InvestigationJob)
                .where(InvestigationJob.id == job_id)
                .where(InvestigationJob.status == "running")
                .where(InvestigationJob.lease_owner == WORKER_ID)
                .values(lease_expires_at=_now() + timedelta(seconds=settings.worker_lease_ttl_seconds))
            )
            await session.commit()


async def run_job(job_id: int) -> None:
    async with AsyncSessionLocal() as session:
        ENGINE_IN_FLIGHT.inc()
        try:
            job = await session.get(InvestigationJob, job_id)
            if job is None:
                return
            investigation = await session.get(Investigation, job.investigation_id)
            if investigation is None:
                job.status = "dead"
                job.last_error_code = "MissingInvestigation"
                await session.commit()
                return
            heartbeat = asyncio.create_task(_heartbeat(job.id))
            try:
                await run_investigation(investigation.id, session)
                job.status = "succeeded"
                job.finished_at = _now()
                job.lease_owner = None
                job.lease_expires_at = None
                ANALYSES.labels(result=investigation.status).inc()
                await session.commit()
            except Exception as exc:  # persist terminal evidence of orchestration failures
                logger.exception("investigation %s failed", investigation.public_id)
                job.last_error_code = type(exc).__name__
                job.last_error_detail = str(exc)[:1000]
                job.lease_owner = None
                job.lease_expires_at = None
                if _retryable(exc) and job.attempt < job.max_attempts:
                    job.status = "retry_wait"
                    job.available_at = _now() + timedelta(seconds=settings.job_base_retry_delay * (2 ** max(0, job.attempt - 1)))
                    investigation.status = "queued"
                else:
                    job.status = "dead"
                    job.finished_at = _now()
                    investigation.status = "failed"
                    investigation.finished_at = _now()
                await session.commit()
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
        finally:
            ENGINE_IN_FLIGHT.dec()


async def main() -> None:
    semaphore = asyncio.Semaphore(settings.engine_concurrency)
    async with AsyncSessionLocal() as session:
        await reclaim_expired_leases(session)
    while True:
        async with AsyncSessionLocal() as session:
            job = await claim_job(session)
        if job is None:
            await asyncio.sleep(settings.worker_poll_interval_seconds)
            continue
        await semaphore.acquire()
        task = asyncio.create_task(run_job(job.id))
        task.add_done_callback(lambda _task: semaphore.release())


if __name__ == "__main__":
    asyncio.run(main())
