"""Durable investigation worker using the current job and lease contracts."""

from __future__ import annotations

import asyncio
import logging
import platform
import uuid
from collections.abc import Awaitable, Callable

from lode.config import settings
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.investigation_leases import (
    ClaimedInvestigationJob,
    InvestigationLeaseStore,
)
from lode.metrics import ENGINE_IN_FLIGHT, INVESTIGATIONS

logger = logging.getLogger("lode.worker")
WORKER_ID = f"{platform.node()}:{uuid.uuid4().hex[:8]}"
InvestigationHandler = Callable[[int], Awaitable[None]]


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def lease_store() -> InvestigationLeaseStore:
    return InvestigationLeaseStore(
        AsyncSessionLocal,
        owner=WORKER_ID,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )


async def _heartbeat(store: InvestigationLeaseStore, job_id: int) -> None:
    interval = max(1.0, settings.worker_lease_ttl_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        if not await store.heartbeat(job_id):
            raise RuntimeError("investigation lease ownership was lost")


async def run_job(
    job: ClaimedInvestigationJob,
    handler: InvestigationHandler,
    *,
    store: InvestigationLeaseStore | None = None,
) -> None:
    durable = store or lease_store()
    ENGINE_IN_FLIGHT.inc()
    heartbeat = asyncio.create_task(_heartbeat(durable, job.job_id))
    try:
        await handler(job.investigation_id)
        await durable.complete(job.job_id)
        INVESTIGATIONS.labels(result="completed").inc()
    except Exception as exc:
        logger.exception("investigation %s failed", job.investigation_id)
        outcome = await durable.fail(
            job.job_id,
            exc,
            retryable=_retryable(exc),
            max_attempts=settings.job_max_attempts,
            base_delay_seconds=settings.job_base_retry_delay,
        )
        INVESTIGATIONS.labels(result=outcome).inc()
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        ENGINE_IN_FLIGHT.dec()


async def main(handler: InvestigationHandler | None = None) -> None:
    if handler is None:
        raise RuntimeError("an InvestigationHandler must be composed before starting the worker")
    durable = lease_store()
    await durable.reclaim_expired()
    semaphore = asyncio.Semaphore(settings.engine_concurrency)
    running: set[asyncio.Task[None]] = set()
    while True:
        job = await durable.claim()
        if job is None:
            await asyncio.sleep(settings.worker_poll_interval_seconds)
            continue
        await semaphore.acquire()
        task = asyncio.create_task(run_job(job, handler, store=durable))
        running.add(task)

        def release(completed: asyncio.Task[None]) -> None:
            running.discard(completed)
            semaphore.release()

        task.add_done_callback(release)


if __name__ == "__main__":
    asyncio.run(main())
