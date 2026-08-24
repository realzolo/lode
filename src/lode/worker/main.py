"""Analysis worker: claim queued jobs and execute them durably.

This replaces the old in-process ``asyncio.create_task`` execution. Jobs are
persisted by the consumer (``analysis_jobs``); this worker claims them with
``SELECT ... FOR UPDATE SKIP LOCKED``, leases them, and runs the engine. A
crashed worker simply lets its leases expire so another worker reclaims the
work — no analysis is ever lost.

Concurrency is bounded by ``settings.engine_concurrency`` so a redelivery burst
cannot exhaust the DB connection pool or trip the LLM provider's rate limit.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lode.config import settings
from lode.db.models.analysis import Analysis
from lode.db.models.application import Application
from lode.db.models.intake import AnalysisJob, Incident
from lode.db.session import AsyncSessionLocal
from lode.engine import run_analysis
from lode.metrics import ANALYSES, ENGINE_IN_FLIGHT

logger = logging.getLogger("lode.worker")

WORKER_ID = f"{platform.node()}:{uuid.uuid4().hex[:8]}"

# Errors that are worth retrying (transient). Anything else is treated as a
# permanent failure and the job is declared dead after exhausting attempts.
_RETRYABLE = (
    TimeoutError,
    ConnectionError,
    OSError,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__
    if isinstance(exc, _RETRYABLE):
        return True
    # Network/provider style errors surfaced as text (httpx, aiokafka, etc.).
    text = str(exc).lower()
    return any(
        token in text
        for token in ("timeout", "timed out", "connection", "503", "502", "500", "reset")
    )


async def reclaim_expired_leases(session: AsyncSession) -> int:
    """Requeue jobs whose lease expired (crashed worker recovery)."""
    result = await session.execute(
        update(AnalysisJob)
        .where(AnalysisJob.status == "running")
        .where(AnalysisJob.lease_expires_at < _now())
        .values(
            status="queued",
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    updated = result.rowcount or 0
    await session.execute(
        update(Analysis)
        .where(Analysis.status == "running")
        .values(status="pending")
    )
    if updated:
        await session.commit()
        logger.warning("reclaimed %d expired lease job(s)", updated)
    return updated


async def claim_job(session: AsyncSession) -> AnalysisJob | None:
    """Atomically claim one due job using a row lock that skips locked rows."""
    result = await session.execute(_claimable_jobs_query())
    job = result.scalars().first()
    if job is None:
        return None

    job.status = "running"
    job.attempt = (job.attempt or 0) + 1
    job.lease_owner = WORKER_ID
    job.lease_expires_at = _now() + _lease_timedelta()
    if job.started_at is None:
        job.started_at = _now()
    await session.commit()
    return job


def _claimable_jobs_query():
    """Build the shared claim query; kept separate for structural tests."""
    return (
        select(AnalysisJob)
        .join(Incident, Incident.id == AnalysisJob.incident_id)
        .join(Application, Application.id == Incident.application_id)
        .where(AnalysisJob.status.in_(["queued", "retry_wait"]))
        .where(AnalysisJob.available_at <= _now())
        .where(Application.ingestion_state == "active")
        .order_by(AnalysisJob.priority.desc(), AnalysisJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _lease_timedelta():
    from datetime import timedelta

    return timedelta(seconds=settings.worker_lease_ttl_seconds)


def _backoff_timedelta(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)


async def run_job(job_id: int) -> None:
    """Execute a single job end-to-end and update its terminal state."""
    async with AsyncSessionLocal() as session:
        ENGINE_IN_FLIGHT.inc()
        try:
            job = (
                await session.execute(
                    select(AnalysisJob).where(AnalysisJob.id == job_id)
                )
            ).scalars().first()
            if job is None:
                return

            analysis = (
                await session.execute(
                    select(Analysis).where(Analysis.id == job.analysis_id)
                )
            ).scalars().first()
            if analysis is not None and analysis.status != "completed":
                analysis.status = "running"
                analysis.started_at = _now()
                await session.commit()

            heartbeat = asyncio.create_task(_heartbeat(job_id))
            try:
                await run_analysis(job.analysis_id, session)
                job.status = "succeeded"
                job.finished_at = _now()
                job.lease_expires_at = None
                job.lease_owner = None
                if analysis is not None:
                    analysis.job_id = job.id
                ANALYSES.labels(result="completed").inc()
                # Flush the terminal status before adding a successor so the
                # partial active-job uniqueness index remains satisfied.
                await session.flush()
                await _enqueue_deferred_reanalysis(session, analysis)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - classify then persist
                logger.exception("analysis %s failed: %s", job.analysis_id, exc)
                terminal = await _fail_or_retry(session, job, analysis, exc)
                if terminal:
                    await _enqueue_deferred_reanalysis(session, analysis)
                    await session.commit()
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
        finally:
            ENGINE_IN_FLIGHT.dec()


async def _enqueue_deferred_reanalysis(
    session: AsyncSession, analysis: Analysis | None
) -> bool:
    """Create exactly one user-confirmed successor after a terminal run."""
    if analysis is None or analysis.incident_id is None:
        return False
    incident = (
        await session.execute(
            select(Incident)
            .where(Incident.id == analysis.incident_id)
            .with_for_update()
        )
    ).scalars().first()
    if incident is None or incident.reanalysis_requested_at is None:
        return False

    requested_by = incident.reanalysis_requested_by
    incident.reanalysis_requested_at = None
    incident.reanalysis_requested_by = None
    successor = Analysis(
        dedupe_key=analysis.dedupe_key,
        application_id=analysis.application_id,
        alert_id=analysis.alert_id,
        incident_id=incident.id,
        status="pending",
        engine_version=None,
    )
    session.add(successor)
    await session.flush()
    session.add(
        AnalysisJob(
            public_id=uuid.uuid4().hex,
            incident_id=incident.id,
            analysis_id=successor.id,
            trigger="guidance_reanalyze",
            status="queued",
            requested_by=requested_by,
            max_attempts=settings.job_max_attempts,
        )
    )
    logger.info("queued deferred re-analysis for incident %s", incident.id)
    return True


async def _fail_or_retry(
    session: AsyncSession, job: AnalysisJob, analysis: Analysis | None, exc: Exception
) -> bool:
    job.last_error_code = type(exc).__name__
    job.last_error_detail = str(exc)[:500]
    retryable = _is_retryable(exc)
    if retryable and (job.attempt or 0) < (job.max_attempts or settings.job_max_attempts):
        # Exponential backoff: base * 2^(attempt-1).
        delay = settings.job_base_retry_delay * (2 ** max(0, (job.attempt or 1) - 1))
        job.status = "retry_wait"
        job.available_at = _now() + _backoff_timedelta(delay)
        job.lease_owner = None
        job.lease_expires_at = None
        if analysis is not None and analysis.status != "completed":
            analysis.status = "pending"
        logger.warning(
            "job %s scheduled retry %d/%d in %.0fs",
            job.id, job.attempt, job.max_attempts, delay,
        )
    else:
        job.status = "dead"
        job.finished_at = _now()
        job.lease_owner = None
        job.lease_expires_at = None
        if analysis is not None and analysis.status != "completed":
            analysis.status = "failed"
            analysis.failure_code = job.last_error_code
            analysis.failure_detail = job.last_error_detail
        ANALYSES.labels(result="failed").inc()
        logger.error("job %s dead after %d attempt(s)", job.id, job.attempt)
    await session.commit()
    return not retryable or (job.attempt or 0) >= (job.max_attempts or settings.job_max_attempts)


async def _heartbeat(job_id: int) -> None:
    """Extend the lease while a long-running analysis is in flight."""
    while True:
        await asyncio.sleep(max(30, settings.worker_lease_ttl_seconds // 3))
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(AnalysisJob)
                    .where(AnalysisJob.id == job_id)
                    .where(AnalysisJob.status == "running")
                    .values(lease_expires_at=_now() + _lease_timedelta())
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat failed for job %s", job_id)


async def run_forever() -> None:
    logger.info("worker %s starting (concurrency=%d)", WORKER_ID, settings.engine_concurrency)
    semaphore = asyncio.Semaphore(settings.engine_concurrency)
    stop = asyncio.Event()

    async def _recover_once() -> None:
        async with AsyncSessionLocal() as session:
            await reclaim_expired_leases(session)

    async def _loop() -> None:
        # Recover once on start, then continuously claim due jobs.
        await _recover_once()
        while not stop.is_set():
            try:
                async with AsyncSessionLocal() as session:
                    job = await claim_job(session)
                if job is None:
                    await asyncio.sleep(settings.worker_poll_interval_seconds)
                    continue
                async with semaphore:
                    await run_job(job.id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("claim loop error; backing off")
                await asyncio.sleep(settings.worker_poll_interval_seconds * 5)

    tasks = [asyncio.create_task(_loop()) for _ in range(max(1, settings.engine_concurrency))]
    try:
        await asyncio.gather(*tasks)
    finally:
        stop.set()


def main() -> None:
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("worker shutting down")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
