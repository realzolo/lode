"""Skip-locked job claims and investigation-scoped lease recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.db.models import (
    Investigation,
    InvestigationJob,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationStep,
)
from lode.masking import mask_structure
from lode.metrics import JOB_CLAIM_LATENCY, JOB_QUEUE_DEPTH, LEASE_RECOVERIES


@dataclass(frozen=True, slots=True)
class ClaimedInvestigationJob:
    job_id: int
    investigation_id: int
    attempt_count: int
    lease_expires_at: datetime


class InvestigationLeaseStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        owner: str,
        lease_ttl_seconds: int,
    ) -> None:
        if not owner or lease_ttl_seconds < 3:
            raise ValueError("lease owner and TTL are required")
        self.session_factory = session_factory
        self.owner = owner
        self.lease_ttl_seconds = lease_ttl_seconds

    async def claim(self, *, now: datetime | None = None) -> ClaimedInvestigationJob | None:
        started = monotonic()
        current = now or datetime.now(UTC)
        try:
            async with self.session_factory() as session:
                queue_depth = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(InvestigationJob)
                            .join(
                                Investigation,
                                Investigation.id == InvestigationJob.investigation_id,
                            )
                            .where(
                                InvestigationJob.status == "pending",
                                InvestigationJob.available_at <= current,
                                Investigation.status.in_(("queued", "running")),
                                (
                                    Investigation.lease_expires_at.is_(None)
                                    | (Investigation.lease_expires_at < current)
                                    | (Investigation.lease_owner == self.owner)
                                ),
                            )
                        )
                    ).scalar_one()
                )
                JOB_QUEUE_DEPTH.set(queue_depth)
                job = (
                    (
                        await session.execute(
                            select(InvestigationJob)
                            .join(
                                Investigation,
                                Investigation.id == InvestigationJob.investigation_id,
                            )
                            .where(
                                InvestigationJob.status == "pending",
                                InvestigationJob.available_at <= current,
                                Investigation.status.in_(("queued", "running")),
                                (
                                    Investigation.lease_expires_at.is_(None)
                                    | (Investigation.lease_expires_at < current)
                                    | (Investigation.lease_owner == self.owner)
                                ),
                            )
                            .order_by(
                                InvestigationJob.available_at,
                                InvestigationJob.created_at,
                            )
                            .limit(1)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .first()
                )
                if job is None:
                    await session.commit()
                    return None
                investigation = (
                    await session.execute(
                        select(Investigation)
                        .where(Investigation.id == job.investigation_id)
                        .with_for_update()
                    )
                ).scalar_one()
                expires = current + timedelta(seconds=self.lease_ttl_seconds)
                job.status = "running"
                job.claimed_by = self.owner
                job.lease_expires_at = expires
                job.attempt_count += 1
                investigation.status = "running"
                investigation.lease_owner = self.owner
                investigation.lease_expires_at = expires
                if investigation.started_at is None:
                    investigation.started_at = current
                await session.commit()
                JOB_QUEUE_DEPTH.set(max(0, queue_depth - 1))
                return ClaimedInvestigationJob(
                    job.id, job.investigation_id, job.attempt_count, expires
                )
        finally:
            JOB_CLAIM_LATENCY.observe(monotonic() - started)

    async def heartbeat(self, job_id: int, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        expires = current + timedelta(seconds=self.lease_ttl_seconds)
        async with self.session_factory() as session:
            job = (
                await session.execute(
                    select(InvestigationJob)
                    .where(
                        InvestigationJob.id == job_id,
                        InvestigationJob.status == "running",
                        InvestigationJob.claimed_by == self.owner,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                await session.commit()
                return False
            investigation = (
                await session.execute(
                    select(Investigation)
                    .where(
                        Investigation.id == job.investigation_id,
                        Investigation.lease_owner == self.owner,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if investigation is None:
                await session.rollback()
                return False
            job.lease_expires_at = expires
            investigation.lease_expires_at = expires
            await session.commit()
            return True

    async def complete(self, job_id: int) -> None:
        async with self.session_factory() as session:
            job = await self._owned_job(session, job_id)
            job.status = "completed"
            job.claimed_by = None
            job.lease_expires_at = None
            investigation = await session.get(Investigation, job.investigation_id)
            if investigation is not None:
                investigation.lease_owner = None
                investigation.lease_expires_at = None
            await session.commit()

    async def fail(
        self,
        job_id: int,
        exc: Exception,
        *,
        retryable: bool,
        max_attempts: int,
        base_delay_seconds: float,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(UTC)
        async with self.session_factory() as session:
            job = await self._owned_job(session, job_id)
            investigation = await session.get(Investigation, job.investigation_id)
            masked, _ = mask_structure({"message": str(exc)[:1_000]})
            job.last_error_code = type(exc).__name__
            job.last_error_detail = masked
            job.claimed_by = None
            job.lease_expires_at = None
            if retryable and job.attempt_count < max_attempts:
                job.status = "pending"
                job.available_at = current + timedelta(
                    seconds=base_delay_seconds * (2 ** max(0, job.attempt_count - 1))
                )
                if investigation is not None:
                    investigation.status = "queued"
                    investigation.lease_owner = None
                    investigation.lease_expires_at = None
                outcome = "pending"
            else:
                job.status = "failed"
                if investigation is not None:
                    investigation.status = "failed"
                    investigation.finished_at = current
                    investigation.lease_owner = None
                    investigation.lease_expires_at = None
                outcome = "failed"
            await session.commit()
            return outcome

    async def reclaim_expired(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        async with self.session_factory() as session:
            jobs = tuple(
                (
                    await session.execute(
                        select(InvestigationJob)
                        .where(
                            InvestigationJob.status == "running",
                            InvestigationJob.lease_expires_at < current,
                        )
                        .order_by(InvestigationJob.id)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                investigation = (
                    await session.execute(
                        select(Investigation)
                        .where(Investigation.id == job.investigation_id)
                        .with_for_update()
                    )
                ).scalar_one()
                running_operations = tuple(
                    (
                        await session.execute(
                            select(InvestigationOperation)
                            .where(
                                InvestigationOperation.investigation_id == investigation.id,
                                InvestigationOperation.status == "running",
                            )
                            .order_by(InvestigationOperation.id)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                for operation in running_operations:
                    operation.status = "interrupted"
                    operation.failure_code = "lease_expired"
                    operation.failure_detail = {"previous_owner": job.claimed_by}
                    operation.finished_at = current
                    investigation.event_cursor += 1
                    session.add(
                        InvestigationOperationEvent(
                            investigation_id=investigation.id,
                            operation_id=operation.id,
                            sequence=investigation.event_cursor,
                            event_name="operation.finished",
                            message="Operation interrupted after its worker lease expired.",
                            detail_masked={"failure_code": "lease_expired"},
                            evidence_refs=[],
                            occurred_at=current,
                        )
                    )
                await session.execute(
                    update(InvestigationStep)
                    .where(
                        InvestigationStep.investigation_id == investigation.id,
                        InvestigationStep.status == "running",
                    )
                    .values(
                        status="interrupted",
                        failure_code="lease_expired",
                        failure_detail={"previous_owner": job.claimed_by},
                        finished_at=current,
                    )
                )
                job.status = "pending"
                job.available_at = current
                job.claimed_by = None
                job.lease_expires_at = None
                investigation.status = "queued"
                investigation.lease_owner = None
                investigation.lease_expires_at = None
            await session.commit()
            LEASE_RECOVERIES.inc(len(jobs))
            return len(jobs)

    async def _owned_job(self, session: AsyncSession, job_id: int) -> InvestigationJob:
        job = (
            await session.execute(
                select(InvestigationJob)
                .where(
                    InvestigationJob.id == job_id,
                    InvestigationJob.status == "running",
                    InvestigationJob.claimed_by == self.owner,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            raise RuntimeError("worker no longer owns the investigation job")
        return job
