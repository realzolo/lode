"""PostgreSQL durable boundaries for serial decision waves."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.application.investigation import (
    InvestigationState,
    PreparedOperation,
    PreparedWave,
)
from lode.db.models import (
    EvidenceArtifact,
    EvidenceAssertion,
    Investigation,
    InvestigationDecision as InvestigationDecisionRow,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationStep,
    SealedEvidenceValue,
)
from lode.domain.investigation import (
    CapabilityEntry,
    DecisionBudget,
    EvaluatedDecision,
    Hypothesis,
    OperationResult,
    PlannedOperation,
    PolicyDecision,
)
from lode.infrastructure.investigation_snapshots import ConnectorSnapshotStore
from lode.masking import mask_structure
from lode.metrics import CONNECTOR_SELECTION, DECISION_POLICY, OPERATION_DURATION


class PostgresInvestigationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self.snapshots = ConnectorSnapshotStore(session_factory)

    async def capability_snapshots(self, investigation_id: int):
        return await self.snapshots.capabilities(investigation_id)

    async def static_capabilities(self, investigation_id: int) -> Sequence[CapabilityEntry]:
        return ()

    async def load_state(self, investigation_id: int) -> InvestigationState:
        async with self.session_factory() as session:
            investigation = await session.get(Investigation, investigation_id)
            if investigation is None:
                raise LookupError("investigation does not exist")
            decisions = tuple(
                (
                    await session.execute(
                        select(InvestigationDecisionRow)
                        .where(InvestigationDecisionRow.investigation_id == investigation_id)
                        .order_by(InvestigationDecisionRow.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            operations = tuple(
                (
                    await session.execute(
                        select(InvestigationOperation)
                        .where(InvestigationOperation.investigation_id == investigation_id)
                        .order_by(InvestigationOperation.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            evidence_refs = tuple(
                (
                    await session.execute(
                        select(EvidenceArtifact.id)
                        .where(EvidenceArtifact.investigation_id == investigation_id)
                        .order_by(EvidenceArtifact.id)
                    )
                ).scalars()
            )
            assertions = tuple(
                (
                    await session.execute(
                        select(EvidenceAssertion.id)
                        .where(EvidenceAssertion.investigation_id == investigation_id)
                        .order_by(EvidenceAssertion.id)
                    )
                ).scalars()
            )
            value_refs = frozenset(
                (
                    await session.execute(
                        select(SealedEvidenceValue.value_ref).where(
                            SealedEvidenceValue.investigation_id == investigation_id
                        )
                    )
                ).scalars()
            )
            hypotheses = (
                tuple(_hypothesis(item) for item in decisions[-1].hypotheses) if decisions else ()
            )
            usage = dict(investigation.budget_usage or {})
            configured = dict(investigation.execution_budget or {})
            wave_count = sum(
                row.policy_outcome in {"allow", "trim"} and row.decision == "continue"
                for row in decisions
            )
            max_steps = _positive_int(configured.get("max_evidence_steps"), 12)
            max_operations = max_steps * _positive_int(configured.get("max_parallel_operations"), 4)
            remaining = DecisionBudget(
                remaining_operations=max(
                    0, max_operations - _nonnegative_int(usage.get("operations"))
                ),
                remaining_native_reads=max(
                    0,
                    _positive_int(configured.get("max_native_reads"), 8)
                    - _nonnegative_int(usage.get("native_reads")),
                ),
                remaining_output_bytes=max(
                    0,
                    _positive_int(configured.get("max_output_bytes"), 8 * 1024 * 1024)
                    - _nonnegative_int(usage.get("output_bytes")),
                ),
                remaining_cost=max(
                    0.0,
                    _nonnegative_float(configured.get("max_cost"), 100.0)
                    - _nonnegative_float(usage.get("cost"), 0.0),
                ),
                remaining_timeout_ms=max(
                    0,
                    _positive_int(configured.get("timeout_seconds"), 600) * 1_000
                    - _nonnegative_int(usage.get("duration_ms")),
                ),
            )
            return InvestigationState(
                investigation_id=investigation_id,
                wave_count=wave_count,
                hypotheses=hypotheses,
                evidence_refs=evidence_refs,
                evidence_anchors=(
                    *(("incident.trace_id",) if "incident.trace_id" in value_refs else ()),
                    *(f"assertion:{value}" for value in assertions),
                ),
                allowed_value_refs=value_refs,
                completed_fingerprints=frozenset(
                    row.fingerprint for row in operations if row.status == "succeeded"
                ),
                budget=remaining,
                state_packet={
                    "investigation_id": investigation_id,
                    "hypotheses": [_plain_hypothesis(item) for item in hypotheses],
                    "evidence_refs": list(evidence_refs),
                    "budget_usage": usage,
                },
            )

    async def prepare_wave(
        self, investigation_id: int, decision: EvaluatedDecision
    ) -> PreparedWave:
        async with self.session_factory() as session:
            investigation = (
                await session.execute(
                    select(Investigation)
                    .where(Investigation.id == investigation_id)
                    .with_for_update()
                )
            ).scalar_one()
            existing = (
                await session.execute(
                    select(InvestigationDecisionRow).where(
                        InvestigationDecisionRow.investigation_id == investigation_id,
                        InvestigationDecisionRow.decision_hash == decision.candidate.decision_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                wave = await self._existing_wave(session, existing, decision)
                await session.commit()
                return wave
            running = (
                await session.execute(
                    select(InvestigationStep.id).where(
                        InvestigationStep.investigation_id == investigation_id,
                        InvestigationStep.status == "running",
                    )
                )
            ).scalar_one_or_none()
            if running is not None:
                raise RuntimeError("investigation already has an active decision wave")
            step_ordinal = await _next_ordinal(session, InvestigationStep, investigation_id)
            step = InvestigationStep(
                investigation_id=investigation_id,
                ordinal=step_ordinal,
                objective=decision.candidate.objective,
                status="running",
                hypothesis_snapshot={
                    "hypotheses": [
                        _plain_hypothesis(value) for value in decision.candidate.hypotheses
                    ]
                },
                input_evidence_refs=list(
                    (
                        await session.execute(
                            select(EvidenceArtifact.id)
                            .where(EvidenceArtifact.investigation_id == investigation_id)
                            .order_by(EvidenceArtifact.id)
                        )
                    ).scalars()
                ),
                output_evidence_refs=[],
                started_at=datetime.now(UTC),
            )
            session.add(step)
            await session.flush()
            decision_ordinal = await _next_ordinal(
                session, InvestigationDecisionRow, investigation_id
            )
            decision_row = InvestigationDecisionRow(
                investigation_id=investigation_id,
                step_id=step.id,
                ordinal=decision_ordinal,
                decision=decision.candidate.decision,
                hypotheses=[_plain_hypothesis(value) for value in decision.candidate.hypotheses],
                operation_plan=[_plain_operation(value) for value in decision.candidate.operations],
                next_model_hint=_plain(decision.candidate.next_model_hint),
                policy_outcome=decision.outcome,
                policy_decisions=[_plain_policy(value) for value in decision.policy_decisions],
                selected_operation_count=len(decision.operations),
                decision_hash=decision.candidate.decision_hash,
                model_invocation_id=decision.candidate.model_invocation_id,
            )
            session.add(decision_row)
            await session.flush()
            operation_ordinal = await _next_ordinal(
                session, InvestigationOperation, investigation_id
            )
            prepared: list[PreparedOperation] = []
            for offset, operation in enumerate(decision.operations):
                masked_input, categories = mask_structure(_plain_operation(operation))
                row = InvestigationOperation(
                    investigation_id=investigation_id,
                    step_id=step.id,
                    decision_id=decision_row.id,
                    ordinal=operation_ordinal + offset,
                    wave_ordinal=offset + 1,
                    action_id=operation.action_id,
                    operation_kind=_operation_kind(operation, decision),
                    purpose=operation.purpose,
                    expected_evidence=operation.expected_evidence,
                    evidence_anchors=list(operation.evidence_anchors),
                    selection_reason=operation.selection_reason,
                    stop_condition=operation.stop_condition,
                    input_masked={
                        "operation": masked_input,
                        "masking_categories": list(categories),
                    },
                    fingerprint=operation.fingerprint,
                    status="queued",
                )
                session.add(row)
                await session.flush()
                prepared.append(PreparedOperation(row.id, operation, operation.fingerprint))
            investigation.status = "running"
            if investigation.started_at is None:
                investigation.started_at = datetime.now(UTC)
            await session.commit()
            for policy in decision.policy_decisions:
                DECISION_POLICY.labels(outcome=policy.outcome, code=policy.code).inc()
            native_reads = sum(
                value.operation.native_candidate is not None for value in prepared
            )
            if native_reads:
                CONNECTOR_SELECTION.labels(outcome="selected").inc(native_reads)
            return PreparedWave(investigation_id, step.id, decision_row.id, tuple(prepared))

    async def mark_operation_running(self, operation_id: int) -> None:
        async with self.session_factory() as session:
            operation = (
                await session.execute(
                    select(InvestigationOperation)
                    .where(InvestigationOperation.id == operation_id)
                    .with_for_update()
                )
            ).scalar_one()
            if operation.status == "running":
                await session.commit()
                return
            if operation.status not in {"queued", "interrupted"}:
                raise RuntimeError("terminal operation cannot restart")
            operation.status = "running"
            operation.started_at = datetime.now(UTC)
            await _append_event(
                session,
                operation,
                "operation.started",
                "Operation execution started.",
                {},
                (),
            )
            await session.commit()

    async def finish_operation(
        self, operation_id: int, result: OperationResult, *, reused: bool
    ) -> None:
        async with self.session_factory() as session:
            operation = (
                await session.execute(
                    select(InvestigationOperation)
                    .where(InvestigationOperation.id == operation_id)
                    .with_for_update()
                )
            ).scalar_one()
            if operation.status in {"succeeded", "rejected", "failed"}:
                await session.commit()
                return
            operation.status = result.status
            operation.result_masked = {
                "result": _plain(result.result_masked),
                "evidence_refs": list(result.evidence_refs),
                "reused": reused,
            }
            operation.metrics = _plain(result.metrics)
            operation.failure_code = result.failure_code
            operation.failure_detail = _plain(result.failure_detail)
            operation.finished_at = datetime.now(UTC)
            await _append_event(
                session,
                operation,
                "operation.finished",
                "Operation execution finished.",
                {
                    "status": result.status,
                    "failure_code": result.failure_code,
                    "reused": reused,
                },
                result.evidence_refs,
            )
            await session.commit()
            OPERATION_DURATION.labels(
                operation_kind=operation.operation_kind,
                status=result.status,
            ).observe(_nonnegative_int(result.metrics.get("duration_ms")) / 1_000)

    async def finish_wave(self, wave: PreparedWave, results: Sequence[OperationResult]) -> None:
        async with self.session_factory() as session:
            step = (
                await session.execute(
                    select(InvestigationStep)
                    .where(InvestigationStep.id == wave.step_id)
                    .with_for_update()
                )
            ).scalar_one()
            if step.status in {"succeeded", "partial", "blocked", "failed"}:
                await session.commit()
                return
            investigation = (
                await session.execute(
                    select(Investigation)
                    .where(Investigation.id == wave.investigation_id)
                    .with_for_update()
                )
            ).scalar_one()
            statuses = [result.status for result in results]
            if statuses and all(value == "succeeded" for value in statuses):
                step.status = "succeeded"
            elif any(value == "succeeded" for value in statuses):
                step.status = "partial"
            else:
                step.status = "failed"
            step.output_evidence_refs = sorted(
                {ref for result in results for ref in result.evidence_refs}
            )
            step.finished_at = datetime.now(UTC)
            usage = dict(investigation.budget_usage or {})
            operation_rows = tuple(
                (
                    await session.execute(
                        select(InvestigationOperation).where(
                            InvestigationOperation.step_id == wave.step_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            usage["operations"] = _nonnegative_int(usage.get("operations")) + len(operation_rows)
            usage["native_reads"] = _nonnegative_int(usage.get("native_reads")) + sum(
                row.operation_kind == "native_read" for row in operation_rows
            )
            for key in ("output_bytes", "duration_ms"):
                usage[key] = _nonnegative_int(usage.get(key)) + sum(
                    _nonnegative_int(result.metrics.get(key)) for result in results
                )
            usage["cost"] = _nonnegative_float(usage.get("cost"), 0.0) + sum(
                _nonnegative_float(result.metrics.get("cost"), 0.0) for result in results
            )
            investigation.budget_usage = usage
            await session.commit()

    async def record_rejected_decision(
        self, investigation_id: int, decision: EvaluatedDecision
    ) -> None:
        async with self.session_factory() as session:
            existing = (
                await session.execute(
                    select(InvestigationDecisionRow.id).where(
                        InvestigationDecisionRow.investigation_id == investigation_id,
                        InvestigationDecisionRow.decision_hash == decision.candidate.decision_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            row = InvestigationDecisionRow(
                investigation_id=investigation_id,
                step_id=None,
                ordinal=await _next_ordinal(session, InvestigationDecisionRow, investigation_id),
                decision=decision.candidate.decision,
                hypotheses=[_plain_hypothesis(value) for value in decision.candidate.hypotheses],
                operation_plan=[_plain_operation(value) for value in decision.candidate.operations],
                next_model_hint=_plain(decision.candidate.next_model_hint),
                policy_outcome="reject",
                policy_decisions=[_plain_policy(value) for value in decision.policy_decisions],
                selected_operation_count=len(decision.candidate.operations),
                decision_hash=decision.candidate.decision_hash,
                model_invocation_id=decision.candidate.model_invocation_id,
            )
            session.add(row)
            await session.commit()
            for policy in decision.policy_decisions:
                DECISION_POLICY.labels(outcome=policy.outcome, code=policy.code).inc()

    async def finish_investigation(
        self, investigation_id: int, *, result_state: str, reason: str
    ) -> None:
        if result_state not in {
            "confirmed",
            "hypothesis",
            "insufficient",
            "unavailable",
        }:
            raise ValueError("invalid investigation result state")
        async with self.session_factory() as session:
            investigation = (
                await session.execute(
                    select(Investigation)
                    .where(Investigation.id == investigation_id)
                    .with_for_update()
                )
            ).scalar_one()
            investigation.status = "completed"
            investigation.result_state = result_state
            investigation.finished_at = datetime.now(UTC)
            investigation.lease_owner = None
            investigation.lease_expires_at = None
            usage = dict(investigation.budget_usage or {})
            usage["terminal_reason"] = reason
            investigation.budget_usage = usage
            await session.commit()
            if reason == "planner_finished":
                CONNECTOR_SELECTION.labels(outcome="zero_call").inc()

    async def _existing_wave(
        self,
        session: AsyncSession,
        decision_row: InvestigationDecisionRow,
        decision: EvaluatedDecision,
    ) -> PreparedWave:
        if decision_row.step_id is None:
            raise RuntimeError("rejected decision cannot execute")
        rows = tuple(
            (
                await session.execute(
                    select(InvestigationOperation)
                    .where(InvestigationOperation.decision_id == decision_row.id)
                    .order_by(InvestigationOperation.wave_ordinal)
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != len(decision.operations):
            raise RuntimeError("durable decision operation set changed")
        prepared: list[PreparedOperation] = []
        for row, operation in zip(rows, decision.operations, strict=True):
            if row.fingerprint != operation.fingerprint:
                raise RuntimeError("durable operation fingerprint changed")
            prepared.append(
                PreparedOperation(
                    row.id,
                    operation,
                    row.fingerprint,
                    _terminal_result(row),
                )
            )
        return PreparedWave(
            decision_row.investigation_id,
            decision_row.step_id,
            decision_row.id,
            tuple(prepared),
        )


async def _next_ordinal(session: AsyncSession, model, investigation_id: int) -> int:
    value = (
        await session.execute(
            select(func.coalesce(func.max(model.ordinal), 0)).where(
                model.investigation_id == investigation_id
            )
        )
    ).scalar_one()
    return int(value) + 1


async def _append_event(
    session: AsyncSession,
    operation: InvestigationOperation,
    event_name: str,
    message: str,
    detail: Mapping[str, Any],
    evidence_refs: Sequence[int],
) -> None:
    investigation = (
        await session.execute(
            select(Investigation)
            .where(Investigation.id == operation.investigation_id)
            .with_for_update()
        )
    ).scalar_one()
    investigation.event_cursor += 1
    masked_detail, _ = mask_structure(detail)
    session.add(
        InvestigationOperationEvent(
            investigation_id=operation.investigation_id,
            operation_id=operation.id,
            sequence=investigation.event_cursor,
            event_name=event_name,
            message=message,
            detail_masked=masked_detail,
            evidence_refs=list(evidence_refs),
            occurred_at=datetime.now(UTC),
        )
    )


def _terminal_result(row: InvestigationOperation) -> OperationResult | None:
    if row.status not in {"succeeded", "rejected", "failed"}:
        return None
    persisted = row.result_masked or {}
    return OperationResult(
        status=row.status,
        result_masked=persisted.get("result", {}),
        evidence_refs=tuple(persisted.get("evidence_refs", ())),
        metrics=row.metrics or {},
        failure_code=row.failure_code,
        failure_detail=row.failure_detail,
    )


def _operation_kind(operation: PlannedOperation, decision: EvaluatedDecision) -> str:
    for policy in decision.policy_decisions:
        if policy.action_id == operation.action_id:
            kind = policy.detail.get("operation_kind")
            if isinstance(kind, str):
                return kind
    return "native_read" if operation.native_candidate is not None else "validation"


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list | frozenset | set):
        return [_plain(child) for child in value]
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"value is not JSON compatible: {type(value).__name__}")


def _plain_hypothesis(value: Hypothesis) -> dict[str, Any]:
    return {
        "hypothesis_id": value.hypothesis_id,
        "mechanism": value.mechanism,
        "supporting_evidence_refs": list(value.supporting_evidence_refs),
        "counter_evidence_refs": list(value.counter_evidence_refs),
        "evidence_gaps": list(value.evidence_gaps),
        "confirmation_requested": value.confirmation_requested,
        "counter_evidence_unavailable": value.counter_evidence_unavailable,
    }


def _hypothesis(value: Mapping[str, Any]) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=str(value["hypothesis_id"]),
        mechanism=str(value["mechanism"]),
        supporting_evidence_refs=tuple(value.get("supporting_evidence_refs", ())),
        counter_evidence_refs=tuple(value.get("counter_evidence_refs", ())),
        evidence_gaps=tuple(value.get("evidence_gaps", ())),
        confirmation_requested=bool(value.get("confirmation_requested", False)),
        counter_evidence_unavailable=bool(value.get("counter_evidence_unavailable", False)),
    )


def _plain_operation(value: PlannedOperation) -> dict[str, Any]:
    return {
        "action_id": value.action_id,
        "purpose": value.purpose,
        "expected_evidence": value.expected_evidence,
        "evidence_anchors": list(value.evidence_anchors),
        "supports_hypotheses": list(value.supports_hypotheses),
        "refutes_hypotheses": list(value.refutes_hypotheses),
        "selection_reason": value.selection_reason,
        "stop_condition": value.stop_condition,
        "estimated_cost": value.estimated_cost,
        "native_candidate": _plain(value.native_candidate),
        "depends_on": list(value.depends_on),
    }


def _plain_policy(value: PolicyDecision) -> dict[str, Any]:
    return {
        "code": value.code,
        "outcome": value.outcome,
        "action_id": value.action_id,
        "detail": _plain(value.detail),
    }


def _positive_int(value: Any, default: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default
    )


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _nonnegative_float(value: Any, default: float) -> float:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
        else default
    )


def result_size(value: Mapping[str, Any]) -> int:
    return len(json.dumps(_plain(value), ensure_ascii=False, sort_keys=True).encode())
