"""Dynamic serial-wave investigation orchestration through explicit ports."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from lode.application.capabilities import CapabilityCatalogBuilder, catalog_for_model
from lode.application.decision_policy import DecisionPolicyEngine
from lode.domain.investigation import (
    CapabilityEntry,
    ConnectorCapabilitySnapshot,
    DecisionBudget,
    EvaluatedDecision,
    Hypothesis,
    InvestigationDecision,
    OperationResult,
    PlannedOperation,
    PolicyDecision,
)


@dataclass(frozen=True, slots=True)
class InvestigationState:
    investigation_id: int
    wave_count: int
    hypotheses: tuple[Hypothesis, ...]
    evidence_refs: tuple[int, ...]
    evidence_anchors: tuple[str, ...]
    allowed_value_refs: frozenset[str]
    completed_fingerprints: frozenset[str]
    budget: DecisionBudget
    state_packet: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedOperation:
    operation_id: int
    operation: PlannedOperation
    fingerprint: str
    terminal_result: OperationResult | None = None


@dataclass(frozen=True, slots=True)
class PreparedWave:
    investigation_id: int
    step_id: int
    decision_id: int
    operations: tuple[PreparedOperation, ...]


@dataclass(frozen=True, slots=True)
class WaveResult:
    step_id: int
    decision_id: int
    results: tuple[OperationResult, ...]


@dataclass(frozen=True, slots=True)
class InvestigationRunResult:
    result_state: str
    wave_count: int
    policy_rejections: int
    terminal_reason: str


class Planner(Protocol):
    async def decide(
        self,
        state: InvestigationState,
        catalog: Sequence[Mapping[str, Any]],
        rejection: Sequence[PolicyDecision] = (),
    ) -> InvestigationDecision: ...


class PlannerUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OperationExecutor(Protocol):
    async def execute(self, operation_id: int, operation: PlannedOperation) -> OperationResult: ...


class WaveRepository(Protocol):
    async def prepare_wave(
        self, investigation_id: int, decision: EvaluatedDecision
    ) -> PreparedWave: ...

    async def mark_operation_running(self, operation_id: int) -> None: ...

    async def finish_operation(
        self, operation_id: int, result: OperationResult, *, reused: bool
    ) -> None: ...

    async def finish_wave(self, wave: PreparedWave, results: Sequence[OperationResult]) -> None: ...


class InvestigationRepository(Protocol):
    async def load_state(self, investigation_id: int) -> InvestigationState: ...

    async def capability_snapshots(
        self, investigation_id: int
    ) -> Sequence[ConnectorCapabilitySnapshot]: ...

    async def static_capabilities(self, investigation_id: int) -> Sequence[CapabilityEntry]: ...

    async def record_rejected_decision(
        self, investigation_id: int, decision: EvaluatedDecision
    ) -> None: ...

    async def finish_investigation(
        self, investigation_id: int, *, result_state: str, reason: str
    ) -> None: ...


class DurableWaveCoordinator:
    """Run one non-overlapping wave and commit every sibling independently."""

    def __init__(self, repository: WaveRepository, executor: OperationExecutor) -> None:
        self.repository = repository
        self.executor = executor

    async def execute(self, investigation_id: int, decision: EvaluatedDecision) -> WaveResult:
        if decision.outcome == "reject" or not 1 <= len(decision.operations) <= 4:
            raise ValueError("only an allowed non-empty decision can execute a wave")
        wave = await self.repository.prepare_wave(investigation_id, decision)
        if len(wave.operations) != len(decision.operations):
            raise RuntimeError("prepared wave changed the selected operation set")
        results = await asyncio.gather(
            *(self._execute_one(item) for item in wave.operations),
            return_exceptions=True,
        )
        normalized: list[OperationResult] = []
        for item, result in zip(wave.operations, results, strict=True):
            if isinstance(result, BaseException):
                failure = OperationResult(
                    status="failed",
                    result_masked={},
                    evidence_refs=(),
                    metrics={},
                    failure_code="operation_executor_failure",
                    failure_detail={"exception_type": type(result).__name__},
                )
                await self.repository.finish_operation(item.operation_id, failure, reused=False)
                normalized.append(failure)
            else:
                normalized.append(result)
        await self.repository.finish_wave(wave, normalized)
        return WaveResult(wave.step_id, wave.decision_id, tuple(normalized))

    async def _execute_one(self, item: PreparedOperation) -> OperationResult:
        if item.terminal_result is not None:
            await self.repository.finish_operation(
                item.operation_id, item.terminal_result, reused=True
            )
            return item.terminal_result
        await self.repository.mark_operation_running(item.operation_id)
        result = await self.executor.execute(item.operation_id, item.operation)
        await self.repository.finish_operation(item.operation_id, result, reused=False)
        return result


class InvestigationOrchestrator:
    """Re-plan after each committed wave, with at most one policy repair."""

    def __init__(
        self,
        *,
        planner: Planner,
        repository: InvestigationRepository,
        wave_coordinator: DurableWaveCoordinator,
        catalog_builder: CapabilityCatalogBuilder | None = None,
        decision_policy: DecisionPolicyEngine | None = None,
        max_waves: int = 12,
    ) -> None:
        if not 1 <= max_waves <= 100:
            raise ValueError("max_waves is invalid")
        self.planner = planner
        self.repository = repository
        self.wave_coordinator = wave_coordinator
        self.catalog_builder = catalog_builder or CapabilityCatalogBuilder()
        self.decision_policy = decision_policy or DecisionPolicyEngine()
        self.max_waves = max_waves

    async def run(self, investigation_id: int) -> InvestigationRunResult:
        rejections = 0
        while True:
            state = await self.repository.load_state(investigation_id)
            if state.wave_count >= self.max_waves or state.budget.remaining_operations == 0:
                await self.repository.finish_investigation(
                    investigation_id,
                    result_state="insufficient",
                    reason="investigation_budget_exhausted",
                )
                return InvestigationRunResult(
                    "insufficient",
                    state.wave_count,
                    rejections,
                    "investigation_budget_exhausted",
                )
            snapshots = await self.repository.capability_snapshots(investigation_id)
            static = await self.repository.static_capabilities(investigation_id)
            capabilities = self.catalog_builder.build(
                snapshots,
                budget=state.budget,
                evidence_anchors=state.evidence_anchors,
                static_capabilities=static,
            )
            model_catalog = catalog_for_model(capabilities)
            try:
                candidate = await self.planner.decide(state, model_catalog)
            except PlannerUnavailable as exc:
                await self.repository.finish_investigation(
                    investigation_id,
                    result_state="unavailable",
                    reason=exc.code,
                )
                return InvestigationRunResult("unavailable", state.wave_count, rejections, exc.code)
            evaluated = self.decision_policy.evaluate(
                candidate,
                capabilities,
                budget=state.budget,
                allowed_value_refs=state.allowed_value_refs,
                completed_fingerprints=state.completed_fingerprints,
            )
            if evaluated.outcome == "reject":
                rejections += 1
                try:
                    repaired = await self.planner.decide(
                        state, model_catalog, evaluated.policy_decisions
                    )
                except PlannerUnavailable as exc:
                    await self.repository.finish_investigation(
                        investigation_id,
                        result_state="unavailable",
                        reason=exc.code,
                    )
                    return InvestigationRunResult(
                        "unavailable", state.wave_count, rejections, exc.code
                    )
                evaluated = self.decision_policy.evaluate(
                    repaired,
                    capabilities,
                    budget=state.budget,
                    allowed_value_refs=state.allowed_value_refs,
                    completed_fingerprints=state.completed_fingerprints,
                )
                if evaluated.outcome == "reject":
                    rejections += 1
                    await self.repository.record_rejected_decision(investigation_id, evaluated)
                    await self.repository.finish_investigation(
                        investigation_id,
                        result_state="unavailable",
                        reason="decision_policy_rejected_after_repair",
                    )
                    return InvestigationRunResult(
                        "unavailable",
                        state.wave_count,
                        rejections,
                        "decision_policy_rejected_after_repair",
                    )
            if evaluated.candidate.decision == "finish":
                await self.repository.finish_investigation(
                    investigation_id,
                    result_state=_terminal_state(evaluated.candidate.hypotheses),
                    reason="planner_finished",
                )
                return InvestigationRunResult(
                    _terminal_state(evaluated.candidate.hypotheses),
                    state.wave_count,
                    rejections,
                    "planner_finished",
                )
            await self.wave_coordinator.execute(investigation_id, evaluated)


def _terminal_state(hypotheses: Sequence[Hypothesis]) -> str:
    confirmed = [item for item in hypotheses if item.confirmation_requested]
    if confirmed and all(
        item.counter_evidence_refs or item.counter_evidence_unavailable for item in confirmed
    ):
        return "confirmed"
    return "hypothesis" if hypotheses else "insufficient"


def fingerprints(values: Sequence[PlannedOperation]) -> set[str]:
    return frozenset(value.fingerprint for value in values)
