"""Deterministic worker concurrency, soak, and lease-loss checks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import lode.worker.main as worker
from lode.config import settings
from lode.infrastructure.investigation_leases import ClaimedInvestigationJob


class FakeLeaseStore:
    def __init__(self, count: int, stop: asyncio.Event) -> None:
        now = datetime.now(UTC)
        self.jobs = [
            ClaimedInvestigationJob(index, index, 1, now + timedelta(minutes=1))
            for index in range(1, count + 1)
        ]
        self.stop = stop
        self.claimed = 0
        self.completed = 0
        self.failed: list[tuple[int, bool]] = []
        self.reclaimed = 0
        self.in_flight_claims = 0
        self.maximum_claims = 0

    async def reclaim_expired(self) -> int:
        self.reclaimed += 1
        return 0

    async def claim(self):
        if not self.jobs:
            return None
        self.claimed += 1
        self.in_flight_claims += 1
        self.maximum_claims = max(self.maximum_claims, self.in_flight_claims)
        return self.jobs.pop(0)

    async def heartbeat(self, _job_id: int) -> bool:
        return True

    async def complete(self, _job_id: int) -> None:
        self.completed += 1
        self.in_flight_claims -= 1
        if self.completed == self.claimed and not self.jobs:
            self.stop.set()

    async def fail(self, job_id, _exc, *, retryable, max_attempts, base_delay_seconds):
        del max_attempts, base_delay_seconds
        self.failed.append((job_id, retryable))
        self.in_flight_claims -= 1
        return "pending" if retryable else "failed"


@pytest.mark.asyncio
async def test_worker_soak_never_preclaims_beyond_engine_concurrency(monkeypatch) -> None:
    stop = asyncio.Event()
    store = FakeLeaseStore(100, stop)
    in_flight = 0
    maximum = 0

    async def handler(_investigation_id: int) -> None:
        nonlocal in_flight, maximum
        in_flight += 1
        maximum = max(maximum, in_flight)
        try:
            await asyncio.sleep(0)
        finally:
            in_flight -= 1

    async def quiet_heartbeat(_store, _job_id):
        await asyncio.Future()

    monkeypatch.setattr(settings, "engine_concurrency", 5)
    monkeypatch.setattr(settings, "worker_poll_interval_seconds", 0)
    monkeypatch.setattr(worker, "_heartbeat", quiet_heartbeat)

    await worker.run_worker(handler, store, stop=stop)

    assert store.reclaimed == 1
    assert store.claimed == store.completed == 100
    assert maximum == 5
    assert store.maximum_claims == 5


@pytest.mark.asyncio
async def test_lease_loss_cancels_handler_without_mutating_unowned_job(monkeypatch) -> None:
    stop = asyncio.Event()
    store = FakeLeaseStore(1, stop)
    job = store.jobs.pop()
    cancelled = asyncio.Event()

    async def handler(_investigation_id: int) -> None:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def lost_heartbeat(_store, _job_id):
        raise worker.LeaseOwnershipLost("fixture lease loss")

    monkeypatch.setattr(worker, "_heartbeat", lost_heartbeat)

    await worker.run_job(job, handler, store=store)

    assert cancelled.is_set()
    assert store.completed == 0
    assert store.failed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "retryable"),
    [(TimeoutError("transient"), True), (ValueError("terminal"), False)],
)
async def test_worker_failure_matrix_preserves_retry_classification(
    monkeypatch, failure: Exception, retryable: bool
) -> None:
    stop = asyncio.Event()
    store = FakeLeaseStore(1, stop)
    job = store.jobs.pop()

    async def handler(_investigation_id: int) -> None:
        raise failure

    async def quiet_heartbeat(_store, _job_id):
        await asyncio.Future()

    monkeypatch.setattr(worker, "_heartbeat", quiet_heartbeat)

    await worker.run_job(job, handler, store=store)

    assert store.failed == [(job.job_id, retryable)]


def test_default_runtime_budgets_match_the_final_plan() -> None:
    assert settings.investigation_timeout_seconds == 600
    assert settings.investigation_max_evidence_steps == 12
    assert settings.investigation_max_model_calls == 10
    assert settings.engine_concurrency > 0
