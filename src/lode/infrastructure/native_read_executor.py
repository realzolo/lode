"""Bridge authorized native reads into durable investigation wave results."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.db.models import (
    EvidenceCollection,
    EvidenceReadAttempt,
    Investigation,
    InvestigationConnectorSnapshot,
    InvestigationDecision,
    InvestigationOperation,
    NativeReadCandidate,
)
from lode.domain.investigation import OperationResult, PlannedOperation
from lode.evidence_access.authorizer import EvidenceAccessAuthorizer
from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.kill_switch import EvidenceKillSwitch, configured_kill_switch
from lode.evidence_access.orchestrator import (
    EvidenceExecutionAdapter,
    EvidenceReadOrchestrator,
)
from lode.evidence_access.types import AccessContext, EvidenceExecutionFailure
from lode.evidence_connectors.registry import build_native_policy_registry
from lode.infrastructure.evidence_archive import PostgresEvidenceResultArchiver


class ConnectorAdapterResolver(Protocol):
    async def resolve(
        self,
        session: AsyncSession,
        snapshot: InvestigationConnectorSnapshot,
    ) -> EvidenceExecutionAdapter: ...


class NativeReadOperationExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        resolver: ConnectorAdapterResolver,
        kill_switch: EvidenceKillSwitch | None = None,
        kill_switch_provider: Callable[[], EvidenceKillSwitch] | None = None,
    ) -> None:
        if kill_switch is not None and kill_switch_provider is not None:
            raise ValueError("provide a fixed kill switch or a dynamic provider, not both")
        self.session_factory = session_factory
        self.resolver = resolver
        self.registry = build_native_policy_registry()
        if kill_switch_provider is not None:
            self.kill_switch_provider = kill_switch_provider
        elif kill_switch is not None:
            self.kill_switch_provider = lambda: kill_switch
        else:
            self.kill_switch_provider = configured_kill_switch

    async def execute(self, operation_id: int, operation: PlannedOperation) -> OperationResult:
        if operation.native_candidate is None:
            return _failure("native_candidate_missing")
        async with self.session_factory() as session:
            row = await session.get(InvestigationOperation, operation_id)
            if row is None or row.operation_kind != "native_read":
                return _failure("native_operation_ownership_failed")
            decision = await session.get(InvestigationDecision, row.decision_id)
            snapshot = (
                await session.execute(
                    select(InvestigationConnectorSnapshot).where(
                        InvestigationConnectorSnapshot.investigation_id == row.investigation_id,
                        InvestigationConnectorSnapshot.connector_id
                        == int(operation.native_candidate["connector_id"]),
                    )
                )
            ).scalar_one_or_none()
            investigation = await session.get(Investigation, row.investigation_id)
            if (
                decision is None
                or decision.model_invocation_id is None
                or snapshot is None
                or investigation is None
            ):
                return _failure("native_execution_context_missing")
            try:
                candidate = NativeReadCandidateInput.model_validate(
                    _plain(operation.native_candidate)
                )
            except ValueError:
                return _failure("invalid_native_candidate")
            used = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(NativeReadCandidate)
                        .where(NativeReadCandidate.investigation_id == row.investigation_id)
                    )
                ).scalar_one()
            )
            archived_bytes = int(
                (
                    await session.execute(
                        select(func.coalesce(func.sum(EvidenceCollection.result_bytes), 0)).where(
                            EvidenceCollection.investigation_id == row.investigation_id
                        )
                    )
                ).scalar_one()
            )
            context = AccessContext(
                investigation_id=row.investigation_id,
                operation_id=row.id,
                connector_snapshot_id=snapshot.id,
                model_invocation_id=decision.model_invocation_id,
                workspace_id=investigation.workspace_id,
                connector_id=snapshot.connector_id,
                snapshot_hash=snapshot.snapshot_hash,
                allowed_languages=tuple(snapshot.allowed_languages),
                allowed_evidence_anchors=tuple(row.evidence_anchors),
                scope_config=snapshot.scope_config,
                schema_catalog=snapshot.schema_catalog,
                execution_budget_policy=snapshot.execution_budget_policy,
                investigation_window_start=investigation.window_started_at,
                investigation_window_end=investigation.window_finished_at,
                native_reads_used=used,
                archived_bytes_used=archived_bytes,
            )
            try:
                kill_switch = self.kill_switch_provider()
            except RuntimeError:
                kill_switch = EvidenceKillSwitch(globally_enabled=False)
            authorized = await EvidenceAccessAuthorizer(
                session, self.registry, kill_switch
            ).authorize(candidate, context)
            if authorized.outcome != "allow" or authorized.token is None:
                return OperationResult(
                    "rejected",
                    {"policy_outcome": "reject"},
                    (),
                    {"output_bytes": 0, "duration_ms": 0, "cost": 0.0},
                    authorized.rejection_code or "native_read_rejected",
                    dict(authorized.rejection_detail or {}),
                )
            try:
                adapter = await self.resolver.resolve(session, snapshot)
            except Exception as exc:  # noqa: BLE001 - the authorization still needs a terminal attempt
                adapter = _UnavailableAdapter(type(exc).__name__)
            execution = await EvidenceReadOrchestrator(
                session, PostgresEvidenceResultArchiver(session)
            ).execute(authorized.token, adapter)
            attempt = (
                await session.execute(
                    select(EvidenceReadAttempt)
                    .where(EvidenceReadAttempt.authorized_read_id == authorized.authorized_read_id)
                    .order_by(EvidenceReadAttempt.attempt.desc())
                    .limit(1)
                )
            ).scalar_one()
            duration_ms = max(
                0, int((attempt.finished_at - attempt.started_at).total_seconds() * 1_000)
            )
            if execution.status != "succeeded" or execution.result is None:
                return OperationResult(
                    "failed",
                    {},
                    (),
                    {"output_bytes": 0, "duration_ms": duration_ms, "cost": 0.0},
                    execution.failure_code or "native_execution_failed",
                    {},
                )
            return OperationResult(
                "succeeded",
                {
                    "artifact_refs": list(execution.artifact_refs),
                    "record_count": execution.result.get("record_count"),
                },
                execution.artifact_refs,
                {
                    "output_bytes": len(
                        json.dumps(execution.result, ensure_ascii=False).encode("utf-8")
                    ),
                    "duration_ms": duration_ms,
                    "cost": 0.0,
                },
            )


def _failure(code: str) -> OperationResult:
    return OperationResult(
        "failed",
        {},
        (),
        {"output_bytes": 0, "duration_ms": 0, "cost": 0.0},
        code,
        {},
    )


class _UnavailableAdapter:
    def __init__(self, error_type: str) -> None:
        self.error_type = error_type

    async def preflight(self, _permit):
        raise EvidenceExecutionFailure(
            "provider_unavailable",
            "connector adapter could not be resolved",
            {"error_type": self.error_type},
        )

    async def execute(self, _permit):
        raise AssertionError("an unavailable adapter cannot execute")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(child) for child in value]
    return value
