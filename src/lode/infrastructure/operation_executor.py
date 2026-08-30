"""Dispatch validated investigation operations to their bounded executors."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.db.models import InvestigationOperation
from lode.domain.investigation import OperationResult, PlannedOperation
from lode.infrastructure.native_read_executor import NativeReadOperationExecutor
from lode.infrastructure.source_executor import SourceReadOperationExecutor


class InvestigationOperationExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        native: NativeReadOperationExecutor,
        source: SourceReadOperationExecutor,
    ) -> None:
        self.session_factory = session_factory
        self.native = native
        self.source = source

    async def execute(self, operation_id: int, operation: PlannedOperation) -> OperationResult:
        async with self.session_factory() as session:
            row = await session.get(InvestigationOperation, operation_id)
        if row is None or row.action_id != operation.action_id:
            return _failure("operation_ownership_failed", operation.action_id)
        if row.operation_kind == "native_read":
            return await self.native.execute(operation_id, operation)
        if row.operation_kind == "source_read":
            return await self.source.execute(operation_id, operation)
        return _failure("operation_kind_not_implemented", operation.action_id)


def _failure(code: str, action_id: str) -> OperationResult:
    return OperationResult(
        status="failed",
        result_masked={},
        evidence_refs=(),
        metrics={"output_bytes": 0, "duration_ms": 0, "cost": 0.0},
        failure_code=code,
        failure_detail={"action_id": action_id},
    )
