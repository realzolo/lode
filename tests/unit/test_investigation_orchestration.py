from __future__ import annotations

import asyncio

from lode.application.investigation import (
    DurableWaveCoordinator,
    InvestigationOrchestrator,
    InvestigationState,
    PlannerUnavailable,
    PreparedOperation,
    PreparedWave,
)
from lode.domain.investigation import (
    CapabilityEntry,
    DecisionBudget,
    EvaluatedDecision,
    Hypothesis,
    InvestigationDecision,
    OperationResult,
    PlannedOperation,
)
from lode.domain.types import NativeLanguage


def budget(operations: int = 20):
    return DecisionBudget(operations, 10, 1_000_000, 100.0, 100_000)


def native_capability(action_id: str, snapshot_id: int, connector_id: int):
    return CapabilityEntry(
        action_id=action_id,
        operation_kind="native_read",
        evidence_types=("database_row",),
        evidence_anchors=("incident.trace_id",),
        resource_summary={"tables": {"names": ["orders"], "count": 1}},
        resource_key=f"connector:{connector_id}",
        server_cost=1.0,
        timeout_ms=1_000,
        result_limit=10,
        output_bytes=10_000,
        connector_snapshot_id=snapshot_id,
        connector_id=connector_id,
        native_language=NativeLanguage.SQL,
    )


def native_operation(action_id: str, connector_id: int):
    return PlannedOperation(
        action_id=action_id,
        purpose="Resolve the current evidence gap",
        expected_evidence="A bounded database record",
        evidence_anchors=("incident.trace_id",),
        supports_hypotheses=("h1",),
        refutes_hypotheses=(),
        selection_reason="The newly committed evidence makes this source relevant",
        stop_condition="Stop after the first matching record",
        estimated_cost=0.0,
        native_candidate={
            "schema_version": "native-read-candidate.v1",
            "action_id": action_id,
            "connector_id": connector_id,
            "language": "sql",
            "purpose": "Resolve the current evidence gap",
            "expected_evidence": "A bounded database record",
            "evidence_anchors": ["incident.trace_id"],
            "payload": {"query": "SELECT * FROM orders LIMIT 10"},
            "value_bindings": {},
            "requested_window": None,
            "requested_limit": 10,
            "requested_timeout_ms": 1_000,
        },
    )


def local_operation(action_id: str):
    return PlannedOperation(
        action_id=action_id,
        purpose="Validate the committed evidence",
        expected_evidence="A deterministic validation result",
        evidence_anchors=("assertion:h1",),
        supports_hypotheses=("h1",),
        refutes_hypotheses=(),
        selection_reason="Sibling validations are independent",
        stop_condition="Stop after validation",
        estimated_cost=0.0,
    )


class FakeRepository:
    def __init__(self, capabilities=()):
        self.capabilities = tuple(capabilities)
        self.wave_count = 0
        self.completed: set[str] = set()
        self.finished: tuple[str, str] | None = None
        self.rejected = []
        self.operation_results = []
        self.reused = []

    async def load_state(self, investigation_id):
        return InvestigationState(
            investigation_id,
            self.wave_count,
            (Hypothesis("h1", "The state changed unexpectedly"),),
            tuple(range(1, self.wave_count + 1)),
            ("incident.trace_id",),
            frozenset({"incident.trace_id"}),
            frozenset(self.completed),
            budget(20 - self.wave_count),
            {"wave_count": self.wave_count},
        )

    async def capability_snapshots(self, _investigation_id):
        return ()

    async def static_capabilities(self, _investigation_id):
        return self.capabilities

    async def record_rejected_decision(self, _investigation_id, decision):
        self.rejected.append(decision)

    async def finish_investigation(self, _investigation_id, *, result_state, reason):
        self.finished = (result_state, reason)

    async def prepare_wave(self, investigation_id, decision):
        operations = tuple(
            PreparedOperation(index + 1, value, value.fingerprint)
            for index, value in enumerate(decision.operations)
        )
        return PreparedWave(investigation_id, self.wave_count + 1, self.wave_count + 1, operations)

    async def mark_operation_running(self, _operation_id):
        return None

    async def finish_operation(self, operation_id, result, *, reused):
        self.operation_results.append((operation_id, result))
        self.reused.append(reused)

    async def finish_wave(self, wave, results):
        self.wave_count += 1
        self.completed.update(value.fingerprint for value in wave.operations)


class ChangingPlanner:
    def __init__(self, first, second):
        self.first = first
        self.second = second
        self.catalogs = []

    async def decide(self, state, catalog, rejection=()):
        self.catalogs.append(tuple(value["action_id"] for value in catalog))
        if state.wave_count == 0:
            return InvestigationDecision(
                "continue",
                state.hypotheses,
                (self.first,),
                "Collect initial runtime evidence",
            )
        if state.wave_count == 1:
            return InvestigationDecision(
                "continue",
                state.hypotheses,
                (self.second,),
                "Follow the evidence into a different source",
            )
        return InvestigationDecision(
            "finish", state.hypotheses, (), "Stop after the evidence gap is resolved"
        )


class CaptureExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, operation_id, operation):
        self.calls.append(operation.action_id)
        return OperationResult(
            "succeeded",
            {"action_id": operation.action_id},
            (operation_id,),
            {"output_bytes": 10, "duration_ms": 1, "cost": 1.0},
        )


async def test_new_evidence_can_change_the_next_connector_and_unselected_calls_stay_zero() -> None:
    first = native_operation("native:1:sql", 1)
    second = native_operation("native:2:sql", 2)
    repository = FakeRepository(
        (
            native_capability("native:1:sql", 1, 1),
            native_capability("native:2:sql", 2, 2),
        )
    )
    planner = ChangingPlanner(first, second)
    executor = CaptureExecutor()
    orchestrator = InvestigationOrchestrator(
        planner=planner,
        repository=repository,
        wave_coordinator=DurableWaveCoordinator(repository, executor),
    )

    result = await orchestrator.run(42)

    assert executor.calls == ["native:1:sql", "native:2:sql"]
    assert result.wave_count == 2
    assert repository.finished == ("hypothesis", "planner_finished")
    assert all(set(value) == {"native:1:sql", "native:2:sql"} for value in planner.catalogs)


class FinishWithoutCapabilities:
    async def decide(self, state, catalog, rejection=()):
        assert catalog == ()
        return InvestigationDecision(
            "finish", state.hypotheses, (), "No relevant capability is available"
        )


async def test_no_relevant_capability_finishes_with_zero_external_calls() -> None:
    repository = FakeRepository()
    executor = CaptureExecutor()
    result = await InvestigationOrchestrator(
        planner=FinishWithoutCapabilities(),
        repository=repository,
        wave_coordinator=DurableWaveCoordinator(repository, executor),
    ).run(42)

    assert result.wave_count == 0
    assert executor.calls == []


class UnavailablePlanner:
    async def decide(self, state, catalog, rejection=()):
        raise PlannerUnavailable("model_capability_unavailable")


async def test_model_capability_failure_finishes_unavailable_without_operations() -> None:
    repository = FakeRepository()
    executor = CaptureExecutor()

    result = await InvestigationOrchestrator(
        planner=UnavailablePlanner(),
        repository=repository,
        wave_coordinator=DurableWaveCoordinator(repository, executor),
    ).run(42)

    assert result.result_state == "unavailable"
    assert result.terminal_reason == "model_capability_unavailable"
    assert repository.finished == ("unavailable", "model_capability_unavailable")
    assert executor.calls == []


class ConcurrentExecutor:
    def __init__(self):
        self.in_flight = 0
        self.maximum = 0

    async def execute(self, operation_id, operation):
        self.in_flight += 1
        self.maximum = max(self.maximum, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            if operation_id == 3:
                raise TimeoutError("fixture timeout")
            return OperationResult("succeeded", {}, (operation_id,), {"duration_ms": 20})
        finally:
            self.in_flight -= 1


async def test_wave_runs_at_most_four_and_one_failure_does_not_cancel_siblings() -> None:
    repository = FakeRepository()
    executor = ConcurrentExecutor()
    operations = tuple(local_operation(f"validate:{index}") for index in range(1, 5))
    decision = InvestigationDecision(
        "continue",
        (Hypothesis("h1", "Validate the mechanism"),),
        operations,
        "Run independent validation siblings",
    )
    evaluated = EvaluatedDecision(decision, "allow", operations, (), 0.0, 0, 0)

    result = await DurableWaveCoordinator(repository, executor).execute(42, evaluated)

    assert executor.maximum == 4
    assert [value.status for value in result.results].count("succeeded") == 3
    assert [value.status for value in result.results].count("failed") == 1
    assert len(repository.operation_results) == 4


async def test_terminal_operation_is_reused_without_calling_executor() -> None:
    operation = local_operation("validate:1")
    terminal = OperationResult("succeeded", {"cached": True}, (9,), {})

    class ReuseRepository(FakeRepository):
        async def prepare_wave(self, investigation_id, decision):
            return PreparedWave(
                investigation_id,
                1,
                1,
                (PreparedOperation(1, operation, operation.fingerprint, terminal),),
            )

    repository = ReuseRepository()
    executor = CaptureExecutor()
    candidate = InvestigationDecision(
        "continue",
        (Hypothesis("h1", "Reuse completed evidence"),),
        (operation,),
        "Resume the durable wave",
    )
    evaluated = EvaluatedDecision(candidate, "allow", (operation,), (), 0.0, 0, 0)

    result = await DurableWaveCoordinator(repository, executor).execute(42, evaluated)

    assert result.results == (terminal,)
    assert executor.calls == []
    assert repository.reused == [True]
