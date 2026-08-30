"""Durable repository analysis lease and failure-state tests."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from lode.application.intake import canonical_hash
from lode.db.models import (
    RepositoryAnalysisIssue,
    RepositoryAnalysisJob,
    Workspace,
    WorkspaceArchitectureContextRevision,
)
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.repository_analysis import RepositoryAnalysisLeaseStore


async def test_repository_analysis_job_claims_heartbeats_and_fails_durably() -> None:
    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        workspace = Workspace(
            name=f"Repository analysis {suffix}",
            ingestion_topic=f"repository-analysis-{suffix}",
        )
        session.add(workspace)
        await session.flush()
        context = WorkspaceArchitectureContextRevision(
            workspace_id=workspace.id,
            entries=[],
            revision=1,
        )
        session.add(context)
        await session.flush()
        workspace.architecture_context_revision_id = context.id
        job = RepositoryAnalysisJob(
            workspace_id=workspace.id,
            requested_binding_ids=[1],
            binding_snapshot=[
                {
                    "binding_id": 1,
                    "configuration_revision": 1,
                    "repository_id": 2,
                    "account_connection_id": 3,
                    "analysis_mode": "code",
                    "is_alert_source": True,
                    "branch_mode": "default",
                    "effective_branch": "main",
                }
            ],
            input_hash=canonical_hash({"repository_bindings": [1]}),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    store = RepositoryAnalysisLeaseStore(
        AsyncSessionLocal,
        owner=f"test-{suffix}",
        lease_seconds=30,
    )
    claimed = await store.claim(job_id)
    assert claimed is not None
    assert claimed.job_id == job_id
    assert await store.heartbeat(job_id) is True
    assert await store.fail(job_id, RuntimeError("repository analysis credential is unavailable"))

    async with AsyncSessionLocal() as session:
        failed = await session.get(RepositoryAnalysisJob, job_id)
        assert failed is not None
        assert failed.state == "failed"
        assert failed.failure_code == "repository_access_unavailable"
        assert failed.result_status == "failed"
        assert failed.issue_count == 1
        assert failed.lease_owner is None
        assert failed.finished_at is not None
        issue = await session.scalar(
            select(RepositoryAnalysisIssue).where(
                RepositoryAnalysisIssue.repository_analysis_job_id == job_id
            )
        )
        assert issue is not None
        assert issue.code == "repository_access_unavailable"
