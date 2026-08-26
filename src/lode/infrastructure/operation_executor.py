"""Dispatch validated investigation operations to their bounded executors."""

from __future__ import annotations

from lode.domain.investigation import OperationResult, PlannedOperation
from lode.infrastructure.native_read_executor import NativeReadOperationExecutor
from lode.infrastructure.source_executor import SourceReadOperationExecutor


class InvestigationOperationExecutor:
    def __init__(
        self,
        *,
        native: NativeReadOperationExecutor,
        source: SourceReadOperationExecutor,
    ) -> None:
        self.native = native
        self.source = source

    async def execute(self, operation_id: int, operation: PlannedOperation) -> OperationResult:
        if operation.native_candidate is not None:
            return await self.native.execute(operation_id, operation)
        if operation.action_id.startswith("source:"):
            return await self.source.execute(operation_id, operation)
        return OperationResult(
            status="failed",
            result_masked={},
            evidence_refs=(),
            metrics={"output_bytes": 0, "duration_ms": 0, "cost": 0.0},
            failure_code="operation_kind_not_implemented",
            failure_detail={"action_id": operation.action_id},
        )
