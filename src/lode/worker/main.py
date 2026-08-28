"""Durable investigation worker using the current job and lease contracts."""

from __future__ import annotations

import asyncio
import logging
import platform
import uuid
from collections.abc import Awaitable, Callable
from time import monotonic

from lode.application.investigation import (
    DurableWaveCoordinator,
    InvestigationOrchestrator,
)
from lode.application.model_planner import StructuredInvestigationPlanner
from lode.config import settings
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.connector_resolver import PostgresConnectorAdapterResolver
from lode.infrastructure.investigation_leases import (
    ClaimedInvestigationJob,
    InvestigationLeaseStore,
)
from lode.infrastructure.investigation_planner import (
    AuditedInvestigationDecisionModel,
)
from lode.infrastructure.investigation_reporting import (
    AuditedInvestigationReporter,
    ReportGenerationUnavailable,
)
from lode.infrastructure.investigation_store import PostgresInvestigationStore
from lode.infrastructure.model_runtime import PostgresModelRuntime
from lode.infrastructure.native_read_executor import NativeReadOperationExecutor
from lode.infrastructure.operation_executor import InvestigationOperationExecutor
from lode.infrastructure.report_store import PostgresReportStore
from lode.infrastructure.repository_analysis import (
    ClaimedRepositoryAnalysisJob,
    RepositoryAnalysisLeaseStore,
    RepositoryAnalysisService,
)
from lode.infrastructure.source_executor import SourceReadOperationExecutor
from lode.metrics import ENGINE_IN_FLIGHT, INVESTIGATION_DURATION, INVESTIGATIONS
from lode.runtime_defaults import (
    JOB_BASE_RETRY_DELAY_SECONDS,
    JOB_MAX_ATTEMPTS,
    WORKER_LEASE_TTL_SECONDS,
    WORKER_POLL_INTERVAL_SECONDS,
)

logger = logging.getLogger("lode.worker")
WORKER_ID = f"{platform.node()}:{uuid.uuid4().hex[:8]}"
InvestigationHandler = Callable[[int], Awaitable[None]]


class LeaseOwnershipLost(RuntimeError):
    pass


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def lease_store() -> InvestigationLeaseStore:
    return InvestigationLeaseStore(
        AsyncSessionLocal,
        owner=WORKER_ID,
        lease_ttl_seconds=WORKER_LEASE_TTL_SECONDS,
    )


async def _heartbeat(store: InvestigationLeaseStore, job_id: int) -> None:
    interval = max(1.0, WORKER_LEASE_TTL_SECONDS / 3)
    while True:
        await asyncio.sleep(interval)
        if not await store.heartbeat(job_id):
            raise LeaseOwnershipLost("investigation lease ownership was lost")


async def run_job(
    job: ClaimedInvestigationJob,
    handler: InvestigationHandler,
    *,
    store: InvestigationLeaseStore | None = None,
) -> None:
    durable = store or lease_store()
    started = monotonic()
    result = "interrupted"
    ENGINE_IN_FLIGHT.inc()
    heartbeat = asyncio.create_task(_heartbeat(durable, job.job_id))
    investigation = asyncio.create_task(handler(job.investigation_id))
    try:
        done, _ = await asyncio.wait(
            {heartbeat, investigation}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            await heartbeat
            raise LeaseOwnershipLost("investigation heartbeat stopped unexpectedly")
        await investigation
        await durable.complete(job.job_id)
        result = "completed"
        INVESTIGATIONS.labels(result="completed").inc()
    except LeaseOwnershipLost:
        result = "lease_lost"
        logger.error("investigation %s lost its worker lease", job.investigation_id)
        INVESTIGATIONS.labels(result="lease_lost").inc()
    except Exception as exc:
        logger.exception("investigation %s failed", job.investigation_id)
        outcome = await durable.fail(
            job.job_id,
            exc,
            retryable=_retryable(exc),
            max_attempts=JOB_MAX_ATTEMPTS,
            base_delay_seconds=JOB_BASE_RETRY_DELAY_SECONDS,
        )
        result = outcome
        INVESTIGATIONS.labels(result=outcome).inc()
    finally:
        if not investigation.done():
            investigation.cancel()
        heartbeat.cancel()
        await asyncio.gather(investigation, heartbeat, return_exceptions=True)
        ENGINE_IN_FLIGHT.dec()
        INVESTIGATION_DURATION.labels(result=result).observe(monotonic() - started)


async def run_worker(
    handler: InvestigationHandler,
    durable: InvestigationLeaseStore,
    *,
    stop: asyncio.Event | None = None,
) -> None:
    await durable.reclaim_expired()
    semaphore = asyncio.Semaphore(settings.worker_concurrency)
    running: set[asyncio.Task[None]] = set()
    try:
        while True:
            await semaphore.acquire()
            if stop is not None and stop.is_set():
                semaphore.release()
                if not running:
                    return
                await asyncio.sleep(0)
                continue
            job = await durable.claim()
            if job is None:
                semaphore.release()
                if stop is not None and stop.is_set() and not running:
                    return
                await asyncio.sleep(WORKER_POLL_INTERVAL_SECONDS)
                continue
            task = asyncio.create_task(run_job(job, handler, store=durable))
            running.add(task)

            def release(completed: asyncio.Task[None]) -> None:
                running.discard(completed)
                semaphore.release()

            task.add_done_callback(release)
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


async def main(handler: InvestigationHandler | None = None) -> None:
    await asyncio.gather(
        run_worker(handler or build_handler(), lease_store()),
        run_repository_analysis_worker(),
    )


async def run_repository_analysis_worker(*, stop: asyncio.Event | None = None) -> None:
    durable = RepositoryAnalysisLeaseStore(
        AsyncSessionLocal,
        owner=WORKER_ID,
        lease_seconds=WORKER_LEASE_TTL_SECONDS,
    )
    service = RepositoryAnalysisService(AsyncSessionLocal)
    await durable.reclaim_expired()
    while stop is None or not stop.is_set():
        job = await durable.claim()
        if job is None:
            await asyncio.sleep(WORKER_POLL_INTERVAL_SECONDS)
            continue
        await _run_repository_analysis_job(job, durable, service)


async def _run_repository_analysis_job(
    job: ClaimedRepositoryAnalysisJob,
    durable: RepositoryAnalysisLeaseStore,
    service: RepositoryAnalysisService,
) -> None:
    interval = max(1.0, WORKER_LEASE_TTL_SECONDS / 3)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(interval)
            if not await durable.heartbeat(job.job_id):
                raise LeaseOwnershipLost("repository analysis lease ownership was lost")

    heartbeat_task = asyncio.create_task(heartbeat())
    analysis_task = asyncio.create_task(service.analyze(job.job_id))
    try:
        done, _ = await asyncio.wait(
            {heartbeat_task, analysis_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if heartbeat_task in done:
            await heartbeat_task
            raise LeaseOwnershipLost("repository analysis heartbeat stopped unexpectedly")
        result = await analysis_task
        if not await durable.complete(job.job_id, result):
            raise LeaseOwnershipLost("repository analysis completion lost its lease")
    except LeaseOwnershipLost:
        logger.error("repository analysis %s lost its worker lease", job.job_id)
    except Exception as exc:
        logger.exception("repository analysis %s failed", job.job_id)
        await durable.fail(job.job_id, exc)
    finally:
        if not analysis_task.done():
            analysis_task.cancel()
        heartbeat_task.cancel()
        await asyncio.gather(analysis_task, heartbeat_task, return_exceptions=True)


def build_handler() -> InvestigationHandler:
    runtime = PostgresModelRuntime(AsyncSessionLocal)
    repository = PostgresInvestigationStore(AsyncSessionLocal)
    executor = InvestigationOperationExecutor(
        native=NativeReadOperationExecutor(AsyncSessionLocal, PostgresConnectorAdapterResolver()),
        source=SourceReadOperationExecutor(AsyncSessionLocal),
    )
    orchestrator = InvestigationOrchestrator(
        planner=StructuredInvestigationPlanner(
            AuditedInvestigationDecisionModel(AsyncSessionLocal, runtime)
        ),
        repository=repository,
        wave_coordinator=DurableWaveCoordinator(repository, executor),
    )
    reporter = AuditedInvestigationReporter(AsyncSessionLocal, runtime)

    async def handle(investigation_id: int) -> None:
        result = await orchestrator.run(investigation_id)
        if result.result_state == "unavailable":
            await _publish_unavailable(investigation_id, result.terminal_reason)
            return
        try:
            await reporter.generate(investigation_id)
        except ReportGenerationUnavailable as exc:
            await _publish_unavailable(
                investigation_id,
                exc.code,
                synthesizer_invocation_id=exc.invocation_id,
            )

    return handle


async def _publish_unavailable(
    investigation_id: int,
    reason: str,
    *,
    synthesizer_invocation_id: int | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        await PostgresReportStore(session).publish_unavailable(
            investigation_id=investigation_id,
            reason=reason,
            synthesizer_invocation_id=synthesizer_invocation_id,
        )
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
