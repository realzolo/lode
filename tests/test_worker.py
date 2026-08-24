"""Hermetic tests for the worker's job state machine (no broker/DB).

Covers the branches that are easy to get wrong: retry classification, the
retry-vs-dead decision on failure, and crashed-worker lease reclamation. The
query-heavy ``claim_job`` path is structurally validated by compile + covered
by integration runs against a real database.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from lode.db.models.analysis import Analysis
from lode.db.models.intake import AnalysisJob
from lode.worker import main as worker_main


class FakeSession:
    """Records commit calls; returns a fixed rowcount for the first execute."""

    def __init__(self, reclaim_rowcount: int = 0) -> None:
        self.committed = 0
        self.reclaim_rowcount = reclaim_rowcount

    async def commit(self) -> None:
        self.committed += 1

    async def execute(self, stmt):
        rowcount = self.reclaim_rowcount

        class _Result:
            @property
            def rowcount(self):
                return rowcount

        return _Result()


def _make_job(attempt: int, max_attempts: int) -> AnalysisJob:
    job = AnalysisJob(
        public_id="job-1",
        incident_id=1,
        analysis_id=1,
        status="running",
        attempt=attempt,
        max_attempts=max_attempts,
    )
    return job


def _make_analysis() -> Analysis:
    return Analysis(
        dedupe_key="k",
        application_id=1,
        status="running",
    )


# --- retry classification -------------------------------------------------
@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timed out"),
        ConnectionError("connection refused"),
        OSError("reset"),
        ValueError("Connection reset by peer"),
        RuntimeError("upstream 503 unavailable"),
    ],
)
def test_is_retryable_accepts_transient(exc):
    assert worker_main._is_retryable(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad config"),
        KeyError("missing"),
        RuntimeError("deterministic logic error"),
    ],
)
def test_is_retryable_rejects_permanent(exc):
    assert worker_main._is_retryable(exc) is False


# --- failure routing ------------------------------------------------------
async def test_fail_or_retry_schedules_retry_when_retryable():
    job = _make_job(attempt=1, max_attempts=5)
    analysis = _make_analysis()
    session = FakeSession()
    await worker_main._fail_or_retry(session, job, analysis, TimeoutError("timed out"))

    assert job.status == "retry_wait"
    assert job.attempt == 1
    assert job.lease_owner is None
    # Backoff must push availability into the future.
    assert job.available_at > datetime.now(timezone.utc)
    assert analysis.status == "pending"
    assert session.committed == 1


async def test_fail_or_retry_declares_dead_when_exhausted():
    job = _make_job(attempt=5, max_attempts=5)
    analysis = _make_analysis()
    session = FakeSession()
    await worker_main._fail_or_retry(session, job, analysis, TimeoutError("timed out"))

    assert job.status == "dead"
    assert analysis.status == "failed"
    assert analysis.failure_code == "TimeoutError"
    assert session.committed == 1


async def test_fail_or_retry_declares_dead_on_permanent_error():
    job = _make_job(attempt=1, max_attempts=5)
    analysis = _make_analysis()
    session = FakeSession()
    await worker_main._fail_or_retry(session, job, analysis, ValueError("bad config"))

    assert job.status == "dead"
    assert analysis.status == "failed"
    assert session.committed == 1


async def test_reclaim_expired_leases_resets_running_jobs():
    session = FakeSession(reclaim_rowcount=3)
    reclaimed = await worker_main.reclaim_expired_leases(session)
    assert reclaimed == 3
    assert session.committed == 1


def test_claim_query_only_selects_active_application_jobs():
    sql = str(
        worker_main._claimable_jobs_query().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "applications.ingestion_state = 'active'" in sql
