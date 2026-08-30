"""Execute evidence-grounded source inspection over frozen repository revisions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.crypto import CryptoError, decrypt_secret
from lode.db.models import (
    EvidenceArtifact,
    EvidenceCollection,
    GitAccountCredentialRevision,
    GitRepository,
    InvestigationDescriptorSnapshot,
    InvestigationInput,
    InvestigationOperation,
    InvestigationRepositorySnapshot,
    SourceAssessment,
    SourceRevision,
)
from lode.domain.investigation import OperationResult, PlannedOperation, SourceQuery, canonical_hash
from lode.git_accounts import credential_identity_hash, decode_credential_secret
from lode.infrastructure.git_source import (
    GitCredentialMaterial,
    GitRemoteRevisionResolver,
    GitRevisionResolver,
    GitSourceReader,
    GitSourceUnavailable,
)
from lode.infrastructure.source_store import PostgresSourceStore
from lode.runtime_defaults import SOURCE_GIT_TIMEOUT_SECONDS

_ACTION = re.compile(r"^source:(?P<snapshot>[1-9][0-9]*):inspect$")


class SourceReadOperationExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        reader: GitSourceReader | None = None,
        revision_resolver: GitRevisionResolver | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.reader = reader or GitSourceReader()
        self.revision_resolver = revision_resolver or GitRemoteRevisionResolver()

    async def execute(self, operation_id: int, operation: PlannedOperation) -> OperationResult:
        match = _ACTION.fullmatch(operation.action_id)
        if match is None or operation.source_query is None:
            return _failure("invalid_source_action")
        repository_snapshot_id = int(match.group("snapshot"))
        async with self.session_factory() as session:
            operation_row = await session.get(InvestigationOperation, operation_id)
            snapshot = await session.get(InvestigationRepositorySnapshot, repository_snapshot_id)
            if (
                operation_row is None
                or operation_row.operation_kind != "source_read"
                or snapshot is None
                or snapshot.analysis_mode != "code"
                or operation_row.investigation_id != snapshot.investigation_id
                or operation_row.action_id != operation.action_id
            ):
                return _failure("source_action_ownership_failed")
            incident_input = await session.get(InvestigationInput, operation_row.investigation_id)
            repository = await session.get(GitRepository, snapshot.repository_id)
            if incident_input is None or repository is None:
                return _failure("investigation_input_missing")
            try:
                credential = await _credential(session, snapshot)
                source_query = await _validated_source_query(
                    session,
                    investigation_id=operation_row.investigation_id,
                    selected_snapshot=snapshot,
                    selected_repository=repository,
                    query=operation.source_query,
                    incident_error=incident_input.error,
                )
            except SourceQueryRejected as exc:
                return _failure(exc.code)
            except (GitSourceUnavailable, OSError, ValueError) as exc:
                return _failure("source_read_unavailable", reason=type(exc).__name__)
            investigation_id = operation_row.investigation_id
            revision_origin = snapshot.revision_policy
            revision = snapshot.frozen_revision_sha
            requested_ref = (
                incident_input.source_revision
                if revision_origin == "alert_revision"
                else snapshot.selected_branch
            )
            repo_url = snapshot.repo_url
            stack = _stack(incident_input.error)

        if revision_origin == "bound_branch_head" and revision is None:
            try:
                resolved = await self.revision_resolver.resolve_branch(
                    repo_url=repo_url,
                    branch=requested_ref or "",
                    credential=credential,
                )
            except (GitSourceUnavailable, OSError, ValueError):
                resolved = None
            if resolved is None:
                async with self.session_factory() as session:
                    snapshot = await session.get(
                        InvestigationRepositorySnapshot, repository_snapshot_id
                    )
                    if snapshot is not None:
                        snapshot.revision_authority = "unavailable"
                        snapshot.snapshot_hash = _repository_snapshot_hash(snapshot)
                    await PostgresSourceStore(session).archive(
                        investigation_id=investigation_id,
                        operation_id=operation_id,
                        repository_snapshot_id=repository_snapshot_id,
                        revision_origin="bound_branch_head",
                        requested_ref=requested_ref,
                        resolved_sha=None,
                        hits=(),
                    )
                    await session.commit()
                return _failure("source_revision_unavailable")
            async with self.session_factory() as session:
                locked = (
                    await session.execute(
                        select(InvestigationRepositorySnapshot)
                        .where(InvestigationRepositorySnapshot.id == repository_snapshot_id)
                        .with_for_update()
                    )
                ).scalar_one()
                if locked.frozen_revision_sha is None:
                    locked.frozen_revision_sha = resolved
                    locked.revision_authority = "authoritative"
                    locked.snapshot_hash = _repository_snapshot_hash(locked)
                    revision = resolved
                else:
                    revision = locked.frozen_revision_sha
                await session.commit()
        if revision is None:
            return _failure("source_revision_unavailable")

        query_fingerprint = _source_query_fingerprint(
            repository_snapshot_id=repository_snapshot_id,
            revision=revision,
            source_query=source_query,
        )
        reused = await self._reuse(
            investigation_id=investigation_id,
            repository_snapshot_id=repository_snapshot_id,
            revision_origin=revision_origin,
            revision=revision,
            query_fingerprint=query_fingerprint,
        )
        if reused is not None:
            return reused

        try:
            hits = await self.reader.collect(
                repo_url=repo_url,
                revision=revision,
                credential=credential,
                stack=stack,
                query_terms=(*source_query["terms"], *source_query["symbols"]),
                path_hints=source_query["path_hints"],
            )
        except (GitSourceUnavailable, OSError, ValueError) as exc:
            return _failure("source_read_unavailable", reason=type(exc).__name__)
        async with self.session_factory() as session:
            archived = await PostgresSourceStore(session).archive(
                investigation_id=investigation_id,
                operation_id=operation_id,
                repository_snapshot_id=repository_snapshot_id,
                revision_origin=revision_origin,
                requested_ref=requested_ref,
                resolved_sha=revision,
                hits=hits,
                query_fingerprint=query_fingerprint,
                source_query=source_query,
            )
            await session.commit()
        output_bytes = sum(len(hit.content.encode("utf-8")) for hit in hits)
        return OperationResult(
            status="succeeded",
            result_masked={
                "source_status": archived.status,
                "repository_snapshot_id": repository_snapshot_id,
                "revision": revision,
                "revision_origin": revision_origin,
                "artifact_count": len(archived.artifact_ids),
                "query_fingerprint": query_fingerprint,
                "reused": False,
            },
            evidence_refs=archived.artifact_ids,
            metrics={"output_bytes": output_bytes},
            failure_code=None,
            failure_detail=None,
        )

    async def _reuse(
        self,
        *,
        investigation_id: int,
        repository_snapshot_id: int,
        revision_origin: str,
        revision: str,
        query_fingerprint: str,
    ) -> OperationResult | None:
        async with self.session_factory() as session:
            collection = (
                await session.execute(
                    select(EvidenceCollection).where(
                        EvidenceCollection.investigation_id == investigation_id,
                        EvidenceCollection.fingerprint == query_fingerprint,
                        EvidenceCollection.collection_kind == "source",
                        EvidenceCollection.status.in_(("succeeded", "partial")),
                    )
                )
            ).scalar_one_or_none()
            if collection is None:
                return None
            artifact_ids = tuple(
                (
                    await session.execute(
                        select(EvidenceArtifact.id)
                        .where(EvidenceArtifact.collection_id == collection.id)
                        .order_by(EvidenceArtifact.id)
                    )
                ).scalars()
            )
            assessment = (
                await session.execute(
                    select(SourceAssessment)
                    .join(SourceRevision, SourceRevision.id == SourceAssessment.source_revision_id)
                    .where(
                        SourceRevision.investigation_id == investigation_id,
                        SourceRevision.repository_snapshot_id == repository_snapshot_id,
                        SourceRevision.revision_origin == revision_origin,
                        SourceRevision.resolved_sha == revision,
                    )
                )
            ).scalar_one()
            return OperationResult(
                status="succeeded",
                result_masked={
                    "source_status": assessment.authority_status,
                    "repository_snapshot_id": repository_snapshot_id,
                    "revision": revision,
                    "revision_origin": revision_origin,
                    "artifact_count": len(artifact_ids),
                    "query_fingerprint": query_fingerprint,
                    "reused": True,
                },
                evidence_refs=artifact_ids,
                metrics={"output_bytes": 0},
                failure_code=None,
                failure_detail=None,
            )


class SourceQueryRejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _validated_source_query(
    session: AsyncSession,
    *,
    investigation_id: int,
    selected_snapshot: InvestigationRepositorySnapshot,
    selected_repository: GitRepository,
    query: SourceQuery,
    incident_error: Mapping[str, object],
) -> dict[str, tuple[object, ...]]:
    artifacts = tuple(
        (
            await session.execute(
                select(EvidenceArtifact).where(
                    EvidenceArtifact.investigation_id == investigation_id,
                    EvidenceArtifact.id.in_(query.evidence_refs),
                )
            )
        )
        .scalars()
        .all()
    )
    if {item.id for item in artifacts} != set(query.evidence_refs):
        raise SourceQueryRejected("source_query_evidence_ownership_failed")
    descriptors = tuple(
        (
            await session.execute(
                select(InvestigationDescriptorSnapshot).where(
                    InvestigationDescriptorSnapshot.investigation_id == investigation_id,
                    InvestigationDescriptorSnapshot.descriptor_kind == "repository",
                )
            )
        )
        .scalars()
        .all()
    )
    selected_descriptors = tuple(
        item.content
        for item in descriptors
        if item.content.get("repository_snapshot_id") == selected_snapshot.id
    )
    evidence_documents = [
        incident_error,
        {
            "repository_name": selected_repository.name,
            "repository_full_name": selected_repository.full_name,
        },
        *({"content": item.content_masked, "provenance": item.provenance} for item in artifacts),
        *selected_descriptors,
    ]
    corpus = json.dumps(evidence_documents, ensure_ascii=False, sort_keys=True).casefold()
    terms = _normalize_values(query.terms)
    symbols = _normalize_values(query.symbols)
    path_hints = _normalize_paths(query.path_hints)
    for value in (*terms, *symbols, *path_hints):
        if value.casefold() not in corpus:
            raise SourceQueryRejected("ungrounded_source_query")

    snapshots = tuple(
        (
            await session.execute(
                select(InvestigationRepositorySnapshot, GitRepository)
                .join(
                    GitRepository, GitRepository.id == InvestigationRepositorySnapshot.repository_id
                )
                .where(
                    InvestigationRepositorySnapshot.investigation_id == investigation_id,
                    InvestigationRepositorySnapshot.analysis_mode == "code",
                )
            )
        ).all()
    )
    referenced_corpus = json.dumps(
        [{"content": item.content_masked, "provenance": item.provenance} for item in artifacts],
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    if not _selected_repository_is_relevant(selected_snapshot.id, snapshots, referenced_corpus):
        raise SourceQueryRejected("source_repository_irrelevant")
    return {
        "terms": terms,
        "symbols": symbols,
        "path_hints": path_hints,
        "evidence_refs": tuple(sorted(query.evidence_refs)),
    }


def _repository_aliases(repository: GitRepository) -> tuple[str, ...]:
    raw = {repository.name, repository.full_name, repository.full_name.rsplit("/", 1)[-1]}
    for value in tuple(raw):
        parts = value.split("-")
        if len(parts) > 2:
            raw.add("-".join(parts[1:]))
    return tuple(
        sorted(
            {value.casefold() for value in raw if len(value) >= 4},
            key=len,
            reverse=True,
        )
    )


def _selected_repository_is_relevant(
    selected_snapshot_id: int,
    snapshots: Sequence[tuple[InvestigationRepositorySnapshot, GitRepository]],
    referenced_corpus: str,
) -> bool:
    matched_repositories = {
        snapshot.id
        for snapshot, repository in snapshots
        if any(alias in referenced_corpus for alias in _repository_aliases(repository))
    }
    return not matched_repositories or selected_snapshot_id in matched_repositories


def _source_query_fingerprint(
    *,
    repository_snapshot_id: int,
    revision: str,
    source_query: Mapping[str, Sequence[object]],
) -> str:
    return canonical_hash(
        {
            "repository_snapshot_id": repository_snapshot_id,
            "frozen_sha": revision,
            "source_query": source_query,
        }
    )


def _normalize_values(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(value.strip() for value in values)))


def _normalize_paths(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if "\\" in value or value.startswith("/"):
            raise SourceQueryRejected("invalid_source_path_hint")
        path = PurePosixPath(value)
        if any(part in {"", ".."} for part in path.parts):
            raise SourceQueryRejected("invalid_source_path_hint")
        rendered = path.as_posix()
        if rendered != value:
            raise SourceQueryRejected("invalid_source_path_hint")
        normalized.append(rendered)
    return tuple(sorted(dict.fromkeys(normalized)))


def _repository_snapshot_hash(snapshot: InvestigationRepositorySnapshot) -> str:
    return canonical_hash(
        {
            "repository_binding_id": snapshot.repository_binding_id,
            "repository_id": snapshot.repository_id,
            "account_connection_id": snapshot.account_connection_id,
            "credential_revision_id": snapshot.credential_revision_id,
            "binding_revision": snapshot.binding_revision,
            "analysis_mode": snapshot.analysis_mode,
            "is_alert_source": snapshot.is_alert_source,
            "priority": snapshot.priority,
            "repo_url": snapshot.repo_url,
            "default_branch": snapshot.default_branch,
            "branch_mode": snapshot.branch_mode,
            "selected_branch": snapshot.selected_branch,
            "frozen_revision_sha": snapshot.frozen_revision_sha,
            "revision_policy": snapshot.revision_policy,
            "revision_authority": snapshot.revision_authority,
            "repository_identity_hash": snapshot.repository_identity_hash,
            "credential_identity_hash": snapshot.credential_identity_hash,
        }
    )


async def _credential(
    session: AsyncSession, snapshot: InvestigationRepositorySnapshot
) -> GitCredentialMaterial | None:
    row = await session.get(GitAccountCredentialRevision, snapshot.credential_revision_id)
    if (
        row is None
        or row.account_connection_id != snapshot.account_connection_id
        or row.credential_identity_hash != snapshot.credential_identity_hash
    ):
        raise GitSourceUnavailable("frozen Git credential is no longer available")
    try:
        plaintext = decrypt_secret(row.secret_ciphertext)
        secret = decode_credential_secret(plaintext or "")
    except (CryptoError, ValueError) as exc:
        raise GitSourceUnavailable("frozen Git credential cannot be decrypted") from exc
    if credential_identity_hash(secret) != snapshot.credential_identity_hash:
        raise GitSourceUnavailable("frozen Git credential cannot be decrypted")
    return GitCredentialMaterial("https", secret.username, secret.token)


def _stack(error: Mapping[str, object]) -> str:
    value = error.get("stack")
    return value if isinstance(value, str) else ""


def _failure(code: str, *, reason: str | None = None) -> OperationResult:
    return OperationResult(
        status="failed",
        result_masked={},
        evidence_refs=(),
        metrics={"timeout_ms": int(SOURCE_GIT_TIMEOUT_SECONDS * 1_000)},
        failure_code=code,
        failure_detail={} if reason is None else {"reason": reason},
    )
