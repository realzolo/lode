"""Execute server-owned source inspection actions over frozen repository snapshots."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.crypto import CryptoError, decrypt_secret
from lode.db.models import (
    GitAccountCredentialRevision,
    InvestigationInput,
    InvestigationOperation,
    InvestigationRepositorySnapshot,
)
from lode.domain.investigation import OperationResult, PlannedOperation
from lode.engine.evidence.git import derive_query_terms
from lode.git_accounts import credential_identity_hash, decode_credential_secret
from lode.infrastructure.git_source import (
    GitCredentialMaterial,
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
    ) -> None:
        self.session_factory = session_factory
        self.reader = reader or GitSourceReader()

    async def execute(self, operation_id: int, operation: PlannedOperation) -> OperationResult:
        match = _ACTION.fullmatch(operation.action_id)
        if match is None:
            return _failure("invalid_source_action")
        repository_snapshot_id = int(match.group("snapshot"))
        async with self.session_factory() as session:
            operation_row = await session.get(InvestigationOperation, operation_id)
            snapshot = await session.get(InvestigationRepositorySnapshot, repository_snapshot_id)
            if (
                operation_row is None
                or operation_row.operation_kind != "source_read"
                or snapshot is None
                or operation_row.investigation_id != snapshot.investigation_id
                or operation_row.action_id != operation.action_id
            ):
                return _failure("source_action_ownership_failed")
            incident_input = await session.get(InvestigationInput, operation_row.investigation_id)
            if incident_input is None:
                return _failure("investigation_input_missing")
            revision = snapshot.frozen_candidate_sha
            if revision is None:
                archived = await PostgresSourceStore(session).archive(
                    investigation_id=operation_row.investigation_id,
                    operation_id=operation_id,
                    repository_snapshot_id=snapshot.id,
                    revision_role="repository_search_candidate",
                    requested_ref=snapshot.selected_branch,
                    resolved_sha=None,
                    hits=(),
                )
                await session.commit()
                return OperationResult(
                    status="succeeded",
                    result_masked={
                        "source_status": archived.status,
                        "repository_snapshot_id": snapshot.id,
                    },
                    evidence_refs=(),
                    metrics={"output_bytes": 0},
                    failure_code=None,
                    failure_detail=None,
                )
            try:
                credential = await _credential(session, snapshot)
            except (GitSourceUnavailable, OSError, ValueError) as exc:
                return _failure("source_read_unavailable", reason=type(exc).__name__)
            stack = _stack(incident_input.error)
            terms = derive_query_terms(_incident_terms(incident_input.error))
            investigation_id = operation_row.investigation_id
            role = snapshot.frozen_revision_role
            requested_ref = (
                incident_input.source_revision
                if role == "incident_source"
                else snapshot.selected_branch
            )
            repo_url = snapshot.repo_url
        try:
            hits = await self.reader.collect(
                repo_url=repo_url,
                revision=revision,
                credential=credential,
                stack=stack,
                query_terms=terms,
            )
        except (GitSourceUnavailable, OSError, ValueError) as exc:
            return OperationResult(
                status="failed",
                result_masked={},
                evidence_refs=(),
                metrics={},
                failure_code="source_read_unavailable",
                failure_detail={"reason": type(exc).__name__},
            )
        async with self.session_factory() as session:
            archived = await PostgresSourceStore(session).archive(
                investigation_id=investigation_id,
                operation_id=operation_id,
                repository_snapshot_id=repository_snapshot_id,
                revision_role=role,
                requested_ref=requested_ref,
                resolved_sha=revision,
                hits=hits,
            )
            await session.commit()
        output_bytes = sum(len(hit.content.encode("utf-8")) for hit in hits)
        return OperationResult(
            status="succeeded",
            result_masked={
                "source_status": archived.status,
                "repository_snapshot_id": repository_snapshot_id,
                "revision": revision,
                "artifact_count": len(archived.artifact_ids),
            },
            evidence_refs=archived.artifact_ids,
            metrics={"output_bytes": output_bytes},
            failure_code=None,
            failure_detail=None,
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


def _incident_terms(error: Mapping[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        error_name=error.get("type"),
        error_message=error.get("message"),
        error_cause=error.get("cause"),
        error_properties={},
        fields={},
        scope={},
    )


def _failure(code: str, *, reason: str | None = None) -> OperationResult:
    return OperationResult(
        status="failed",
        result_masked={},
        evidence_refs=(),
        metrics={"timeout_ms": int(SOURCE_GIT_TIMEOUT_SECONDS * 1_000)},
        failure_code=code,
        failure_detail={} if reason is None else {"reason": reason},
    )
