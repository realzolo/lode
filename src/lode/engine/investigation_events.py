"""Rich, append-only operation events for bounded-wave investigations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update

from lode.db.models.investigation import (
    Investigation,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationStep,
    OPERATION_STATUSES,
)
from lode.engine.evidence.secret_mask import mask_secrets


def _safe(value: Any, *, string_limit: int = 2_000) -> Any:
    if isinstance(value, str):
        return mask_secrets(value)[0][:string_limit]
    if isinstance(value, dict):
        return {str(key): _safe(child, string_limit=string_limit) for key, child in value.items()}
    if isinstance(value, list):
        return [_safe(child, string_limit=500) for child in value[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return mask_secrets(str(value))[0][:string_limit]


async def _next_sequence(session, investigation_id: int) -> int:
    value = (
        await session.execute(
            update(Investigation)
            .where(Investigation.id == investigation_id)
            .values(event_cursor=Investigation.event_cursor + 1)
            .returning(Investigation.event_cursor)
        )
    ).scalar_one()
    return int(value)


async def _append(
    session,
    operation: InvestigationOperation,
    *,
    kind: str,
    message: str,
    detail: dict[str, Any] | None = None,
    evidence_refs: list[int] | None = None,
) -> InvestigationOperationEvent:
    event = InvestigationOperationEvent(
        investigation_id=operation.investigation_id,
        step_id=operation.step_id,
        operation_id=operation.id,
        sequence=await _next_sequence(session, operation.investigation_id),
        kind=kind,
        message=_safe(message, string_limit=1_000),
        detail=_safe(detail or {}),
        evidence_refs=[ref for ref in (evidence_refs or []) if isinstance(ref, int)],
        occurred_at=datetime.now(UTC),
    )
    session.add(event)
    await session.flush()
    return event


async def start_operation(
    session,
    *,
    investigation_id: int,
    step_id: int,
    kind: str,
    actor: str,
    title: str,
    purpose: str,
    input_summary: dict[str, Any] | None = None,
    message: str,
    commit: bool = False,
) -> InvestigationOperation:
    ordinal = int(
        (
            await session.execute(
                select(func.coalesce(func.max(InvestigationOperation.ordinal), 0)).where(
                    InvestigationOperation.investigation_id == investigation_id
                )
            )
        ).scalar_one()
    ) + 1
    operation = InvestigationOperation(
        investigation_id=investigation_id,
        step_id=step_id,
        ordinal=ordinal,
        kind=kind,
        actor=actor,
        title=_safe(title, string_limit=200),
        purpose=_safe(purpose, string_limit=1_000),
        input_summary=_safe(input_summary or {}),
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(operation)
    await session.flush()
    await _append(session, operation, kind="started", message=message, detail={"input": operation.input_summary})
    if commit:
        await session.commit()
    return operation


async def start_operations(
    session,
    definitions: list[dict[str, Any]],
    *,
    commit: bool = False,
) -> list[InvestigationOperation]:
    """Allocate a wave's operation ordinals once before concurrent I/O starts."""
    if not definitions:
        return []
    investigation_ids = {int(item["investigation_id"]) for item in definitions}
    if len(investigation_ids) != 1:
        raise ValueError("one operation wave must belong to one investigation")
    investigation_id = investigation_ids.pop()
    first_ordinal = int(
        (
            await session.execute(
                select(func.coalesce(func.max(InvestigationOperation.ordinal), 0)).where(
                    InvestigationOperation.investigation_id == investigation_id
                )
            )
        ).scalar_one()
    ) + 1
    operations: list[InvestigationOperation] = []
    now = datetime.now(UTC)
    for offset, definition in enumerate(definitions):
        operation = InvestigationOperation(
            investigation_id=investigation_id,
            step_id=int(definition["step_id"]),
            ordinal=first_ordinal + offset,
            kind=str(definition["kind"]),
            actor=str(definition["actor"]),
            title=_safe(definition["title"], string_limit=200),
            purpose=_safe(definition["purpose"], string_limit=1_000),
            input_summary=_safe(definition.get("input_summary") or {}),
            status="running",
            started_at=now,
        )
        session.add(operation)
        operations.append(operation)
    await session.flush()
    for operation, definition in zip(operations, definitions, strict=True):
        await _append(
            session,
            operation,
            kind="started",
            message=str(definition["message"]),
            detail={"input": operation.input_summary},
        )
    if commit:
        await session.commit()
    return operations


async def progress_operation(
    session,
    operation: InvestigationOperation,
    *,
    message: str,
    detail: dict[str, Any] | None = None,
    evidence_refs: list[int] | None = None,
    commit: bool = False,
) -> InvestigationOperationEvent:
    event = await _append(
        session,
        operation,
        kind="progress",
        message=message,
        detail=detail,
        evidence_refs=evidence_refs,
    )
    if commit:
        await session.commit()
    return event


async def finish_operation(
    session,
    operation: InvestigationOperation,
    *,
    status: str,
    result_summary: str,
    message: str,
    metrics: dict[str, Any] | None = None,
    evidence_refs: list[int] | None = None,
    failure: Exception | str | None = None,
    commit: bool = False,
) -> InvestigationOperationEvent:
    if status not in OPERATION_STATUSES or status in {"queued", "running"}:
        raise ValueError(f"invalid terminal operation status: {status}")
    operation.status = status
    operation.result_summary = _safe(result_summary, string_limit=2_000)
    operation.metrics = _safe(metrics or {})
    operation.evidence_refs = [ref for ref in (evidence_refs or []) if isinstance(ref, int)]
    operation.finished_at = datetime.now(UTC)
    if failure is not None:
        operation.failure_code = type(failure).__name__ if isinstance(failure, Exception) else "operation_failed"
        operation.failure_detail = _safe(str(failure), string_limit=1_000)
    event = await _append(
        session,
        operation,
        kind="finished",
        message=message,
        detail={
            "status": status,
            "result": operation.result_summary,
            "metrics": operation.metrics,
            "failure_code": operation.failure_code,
            "failure_detail": operation.failure_detail,
        },
        evidence_refs=operation.evidence_refs,
    )
    if commit:
        await session.commit()
    return event


async def start_step(session, step: InvestigationStep, *, commit: bool = False) -> None:
    step.status = "running"
    step.started_at = datetime.now(UTC)
    await session.flush()
    if commit:
        await session.commit()


async def finish_step(
    session,
    step: InvestigationStep,
    *,
    status: str,
    result_summary: str,
    output_refs: list[int] | None = None,
    failure: Exception | str | None = None,
    commit: bool = False,
) -> None:
    if status not in OPERATION_STATUSES or status in {"queued", "running"}:
        raise ValueError(f"invalid terminal step status: {status}")
    step.status = status
    step.result_summary = _safe(result_summary, string_limit=2_000)
    step.output_refs = [ref for ref in (output_refs or []) if isinstance(ref, int)]
    step.finished_at = datetime.now(UTC)
    if failure is not None:
        step.failure_code = type(failure).__name__ if isinstance(failure, Exception) else "step_failed"
        step.failure_detail = _safe(str(failure), string_limit=1_000)
    await session.flush()
    if commit:
        await session.commit()
