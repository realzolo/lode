"""Durable investigation worker using the current job and lease contracts."""

from __future__ import annotations

import asyncio
import logging
import platform
import uuid
from collections.abc import Awaitable, Callable

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
from lode.infrastructure.source_executor import SourceReadOperationExecutor
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
        handler = build_handler()
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
        max_waves=settings.investigation_max_evidence_steps,
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
