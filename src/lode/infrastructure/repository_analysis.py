"""Durable exact-revision repository analysis and ResourceGraph publication."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.crypto import CryptoError, decrypt_secret
from lode.db.models import (
    GitAccount,
    GitAccountCredentialRevision,
    GitAccountRepositoryAccess,
    GitRepository,
    RepositoryAnalysisIssue,
    RepositoryAnalysisJob,
    WorkspaceRepositoryBinding,
)
from lode.git_accounts import credential_identity_hash, decode_credential_secret
from lode.infrastructure.git_source import (
    GitCredentialMaterial,
    GitRemoteRevisionResolver,
    GitRevisionResolver,
    GitSourceReader,
)
from lode.resource_understanding.scanner import ManifestScanner, RepositoryScanLimitError
from lode.resource_understanding.store import BoundRepositoryScan, ResourceGraphStore
from lode.resource_understanding.types import (
    SemanticAnnotationDraft,
    repository_candidate_namespace,
)


@dataclass(frozen=True, slots=True)
class ClaimedRepositoryAnalysisJob:
    job_id: int
    workspace_id: int


@dataclass(frozen=True, slots=True)
class RepositoryAnalysisResult:
    graph_revision_id: int
    source_revisions: dict[str, str]
    source_branches: dict[str, str]
    scanned_file_count: int
    issues: tuple["RepositoryAnalysisIssueDraft", ...]


@dataclass(frozen=True, slots=True)
class RepositoryAnalysisIssueDraft:
    repository_binding_id: int | None
    severity: str
    code: str
    path: str | None
    detail: str


class RepositoryAnalysisLeaseStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        owner: str,
        lease_seconds: int = 300,
    ) -> None:
        self.session_factory = session_factory
        self.owner = owner
        self.lease_seconds = lease_seconds

    async def reclaim_expired(self) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(RepositoryAnalysisJob)
                        .where(
                            RepositoryAnalysisJob.state == "running",
                            RepositoryAnalysisJob.lease_expires_at < now,
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            for row in rows:
                row.state = "queued"
                row.lease_owner = None
                row.lease_expires_at = None
            await session.commit()
            return len(rows)

    async def claim(self, job_id: int | None = None) -> ClaimedRepositoryAnalysisJob | None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            statement = select(RepositoryAnalysisJob).where(
                or_(
                    RepositoryAnalysisJob.state == "queued",
                    (
                        (RepositoryAnalysisJob.state == "running")
                        & (RepositoryAnalysisJob.lease_expires_at < now)
                    ),
                )
            )
            if job_id is not None:
                statement = statement.where(RepositoryAnalysisJob.id == job_id)
            row = (
                await session.execute(
                    statement.order_by(RepositoryAnalysisJob.created_at, RepositoryAnalysisJob.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                await session.rollback()
                return None
            row.state = "running"
            row.attempt += 1
            row.lease_owner = self.owner
            row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            row.started_at = row.started_at or now
            row.finished_at = None
            row.failure_code = None
            await session.commit()
            return ClaimedRepositoryAnalysisJob(row.id, row.workspace_id)

    async def heartbeat(self, job_id: int) -> bool:
        async with self.session_factory() as session:
            row = await session.get(RepositoryAnalysisJob, job_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != self.owner:
                await session.rollback()
                return False
            row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            await session.commit()
            return True

    async def complete(self, job_id: int, result: RepositoryAnalysisResult) -> bool:
        async with self.session_factory() as session:
            row = await session.get(RepositoryAnalysisJob, job_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != self.owner:
                await session.rollback()
                return False
            row.state = "succeeded"
            row.result_status = "warnings" if result.issues else "clean"
            row.source_revisions = result.source_revisions
            row.source_branches = result.source_branches
            row.graph_revision_id = result.graph_revision_id
            row.scanned_file_count = result.scanned_file_count
            row.issue_count = len(result.issues)
            for ordinal, issue in enumerate(result.issues):
                session.add(
                    RepositoryAnalysisIssue(
                        repository_analysis_job_id=row.id,
                        repository_binding_id=issue.repository_binding_id,
                        ordinal=ordinal,
                        severity=issue.severity,
                        code=issue.code,
                        path=issue.path,
                        detail=_safe_detail(issue.detail),
                    )
                )
            row.lease_owner = None
            row.lease_expires_at = None
            row.finished_at = datetime.now(UTC)
            await session.commit()
            return True

    async def fail(self, job_id: int, exc: Exception) -> bool:
        async with self.session_factory() as session:
            row = await session.get(RepositoryAnalysisJob, job_id, with_for_update=True)
            if row is None or row.state != "running" or row.lease_owner != self.owner:
                await session.rollback()
                return False
            row.state = "failed"
            row.result_status = "failed"
            row.failure_code = _failure_code(exc)
            row.issue_count = 1
            session.add(
                RepositoryAnalysisIssue(
                    repository_analysis_job_id=row.id,
                    repository_binding_id=None,
                    ordinal=0,
                    severity="error",
                    code=row.failure_code,
                    path=None,
                    detail=_failure_detail(row.failure_code),
                )
            )
            row.lease_owner = None
            row.lease_expires_at = None
            row.finished_at = datetime.now(UTC)
            await session.commit()
            return True


class RepositoryAnalysisService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        resolver: GitRevisionResolver | None = None,
        reader: GitSourceReader | None = None,
        scanner: ManifestScanner | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.resolver = resolver or GitRemoteRevisionResolver()
        self.reader = reader or GitSourceReader()
        self.scanner = scanner or ManifestScanner()

    async def _scan_repository(self, binding, repository, revision: str, credential):
        return await self.reader.read_checkout(
            repo_url=repository.repo_url,
            revision=revision,
            credential=credential,
            reader=lambda root: self.scanner.scan(
                root,
                revision,
                candidate_namespace=repository_candidate_namespace(binding.id),
            ),
        )

    async def analyze(self, job_id: int) -> RepositoryAnalysisResult:
        async with self.session_factory() as session:
            job = await session.get(RepositoryAnalysisJob, job_id)
            if job is None or job.state != "running":
                raise RuntimeError("repository analysis job is unavailable")
            snapshots = _binding_snapshots(job)
            rows = tuple(
                (
                    await session.execute(
                        select(
                            WorkspaceRepositoryBinding,
                            GitRepository,
                            GitAccount,
                            GitAccountCredentialRevision,
                        )
                        .join(GitRepository, GitRepository.id == WorkspaceRepositoryBinding.repository_id)
                        .join(
                            GitAccount,
                            GitAccount.id == WorkspaceRepositoryBinding.account_connection_id,
                        )
                        .join(
                            GitAccountCredentialRevision,
                            GitAccountCredentialRevision.id == GitAccount.current_credential_revision_id,
                        )
                        .join(
                            GitAccountRepositoryAccess,
                            (GitAccountRepositoryAccess.account_connection_id == GitAccount.id)
                            & (GitAccountRepositoryAccess.repository_id == GitRepository.id),
                        )
                        .where(
                            WorkspaceRepositoryBinding.workspace_id == job.workspace_id,
                            WorkspaceRepositoryBinding.id.in_(job.requested_binding_ids),
                            GitAccount.state == "active",
                            GitAccount.verification_status == "healthy",
                            GitAccountRepositoryAccess.state == "available",
                        )
                        .order_by(WorkspaceRepositoryBinding.id)
                    )
                ).all()
            )
            if {row.id for row, _, _, _ in rows} != set(snapshots):
                raise RuntimeError("repository analysis authorization changed")
            credentials: dict[int, GitCredentialMaterial] = {}
            for binding, repository, account, revision in rows:
                snapshot = snapshots[binding.id]
                if (
                    snapshot["repository_id"] != repository.id
                    or snapshot["account_connection_id"] != account.id
                ):
                    raise RuntimeError("repository analysis configuration changed")
                try:
                    secret = decode_credential_secret(decrypt_secret(revision.secret_ciphertext) or "")
                except (CryptoError, ValueError) as exc:
                    raise RuntimeError("repository analysis credential is unavailable") from exc
                if credential_identity_hash(secret) != revision.credential_identity_hash:
                    raise RuntimeError("repository analysis credential identity changed")
                credentials[binding.id] = GitCredentialMaterial("https", secret.username, secret.token)

        revisions = await asyncio.gather(
            *(
                self.resolver.resolve_branch(
                    repo_url=repository.repo_url,
                    branch=str(snapshots[binding.id]["effective_branch"]),
                    credential=credentials[binding.id],
                )
                for binding, repository, _, _ in rows
            )
        )
        if any(revision is None for revision in revisions):
            raise RuntimeError("repository selected branch could not be resolved")

        semaphore = asyncio.Semaphore(4)

        async def scan_one(binding, repository, revision: str):
            async with semaphore:
                return await self._scan_repository(
                    binding,
                    repository,
                    revision,
                    credentials[binding.id],
                )

        scans = await asyncio.gather(
            *(
                scan_one(binding, repository, revision)
                for (binding, repository, _, _), revision in zip(rows, revisions, strict=True)
                if revision is not None
            )
        )
        bound_scans = tuple(
            BoundRepositoryScan(binding.id, repository.id, scan)
            for (binding, repository, _, _), scan in zip(rows, scans, strict=True)
        )
        annotations = _deterministic_component_annotations(
            {binding_id: str(snapshot["role"]) for binding_id, snapshot in snapshots.items()},
            bound_scans,
        )
        async with self.session_factory() as session:
            published = await ResourceGraphStore(session).publish(
                workspace_id=job.workspace_id,
                scans=bound_scans,
                annotations=annotations,
                runtime_binding_ids={
                    binding_id
                    for binding_id, snapshot in snapshots.items()
                    if snapshot["role"] == "runtime_source"
                },
                allow_inactive_binding_ids=set(snapshots),
                prompt_revision="deterministic-component-projection.1",
            )
        return RepositoryAnalysisResult(
            graph_revision_id=published.revision_id,
            source_revisions={
                str(binding.id): revision
                for (binding, _, _, _), revision in zip(rows, revisions, strict=True)
                if revision is not None
            },
            source_branches={
                str(binding.id): str(snapshots[binding.id]["effective_branch"])
                for binding, _, _, _ in rows
            },
            scanned_file_count=sum(scan.scanned_file_count for scan in scans),
            issues=tuple(
                RepositoryAnalysisIssueDraft(
                    repository_binding_id=binding.id,
                    severity="warning",
                    code=issue.code,
                    path=issue.path,
                    detail=issue.detail,
                )
                for (binding, _, _, _), scan in zip(rows, scans, strict=True)
                for issue in scan.issues
            ),
        )


def _deterministic_component_annotations(
    roles: dict[int, str], scans: tuple[BoundRepositoryScan, ...]
):
    annotations: list[SemanticAnnotationDraft] = []
    for bound in scans:
        if roles[bound.repository_binding_id] != "runtime_source":
            continue
        for unit in bound.scan.build_units:
            names = unit.artifact_hints.get("names", [])
            display_name = (
                str(names[0]).strip()
                if isinstance(names, list) and names and str(names[0]).strip()
                else (unit.source_root if unit.source_root != "." else f"Repository {bound.repository_id}")
            )
            stable_key = f"component:auto-{hashlib.sha256(unit.candidate_key.encode()).hexdigest()[:20]}"
            annotations.append(
                SemanticAnnotationDraft(
                    annotation_kind="component_identity",
                    stable_key=stable_key,
                    display_name=display_name,
                    component_kind="unknown",
                    build_unit_keys=(unit.candidate_key,),
                    observation_refs=unit.observation_refs,
                    aliases=tuple(str(item) for item in names if str(item).strip()),
                    description="Automatically projected from structured repository manifests.",
                )
            )
    return tuple(annotations)


def _failure_code(exc: Exception) -> str:
    text = str(exc)
    if "authorization" in text or "credential" in text:
        return "repository_access_unavailable"
    if "branch" in text:
        return "repository_branch_unavailable"
    if isinstance(exc, RepositoryScanLimitError):
        return "repository_scan_limit_exceeded"
    if "Git" in text or "checkout" in text:
        return "repository_checkout_failed"
    return "repository_analysis_failed"


def _binding_snapshots(job: RepositoryAnalysisJob) -> dict[int, dict]:
    snapshots: dict[int, dict] = {}
    for value in job.binding_snapshot:
        if not isinstance(value, dict):
            raise RuntimeError("repository analysis snapshot is invalid")
        binding_id = value.get("binding_id")
        repository_id = value.get("repository_id")
        account_connection_id = value.get("account_connection_id")
        role = value.get("role")
        branch = value.get("effective_branch")
        if (
            not isinstance(binding_id, int)
            or not isinstance(repository_id, int)
            or not isinstance(account_connection_id, int)
            or not isinstance(role, str)
            or not isinstance(branch, str)
            or not branch
            or binding_id in snapshots
        ):
            raise RuntimeError("repository analysis snapshot is invalid")
        snapshots[binding_id] = value
    if set(snapshots) != set(job.requested_binding_ids):
        raise RuntimeError("repository analysis snapshot is invalid")
    return snapshots


def _safe_detail(value: str) -> str:
    return " ".join(value.split())[:500]


def _failure_detail(code: str) -> str:
    messages = {
        "repository_access_unavailable": "The configured Git account no longer has usable repository access.",
        "repository_branch_unavailable": "The selected branch could not be resolved from the repository.",
        "repository_checkout_failed": "The selected repository revision could not be checked out.",
        "repository_scan_limit_exceeded": "The repository exceeded a server-owned scan safety limit.",
        "repository_analysis_failed": "Repository analysis could not complete.",
    }
    return messages[code]
