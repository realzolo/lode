"""Exercise snapshots, durable waves, graph projection, and lease recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from lode.application.decision_policy import DecisionPolicyEngine
from lode.application.evidence_graph import EvidenceGraphProjector
from lode.application.intake import ManualIncidentRequest, normalize_manual
from lode.application.investigation import DurableWaveCoordinator
from lode.crypto import encrypt_value
from lode.db.models import (
    EvidenceAccessScope,
    EvidenceArtifact,
    EvidenceConnector,
    Investigation,
    InvestigationJob,
    InvestigationOperation,
    InvestigationStep,
    ObservedEntity,
    ObservedEvent,
    ObservedRelation,
    ResourceObservation,
    User,
    Workspace,
)
from lode.db.session import AsyncSessionLocal, engine
from lode.domain.investigation import (
    CapabilityEntry,
    DecisionBudget,
    Hypothesis,
    InvestigationDecision,
    NormalizedLogEvent,
    OperationResult,
    PlannedOperation,
    canonical_hash,
)
from lode.infrastructure.evidence_graph_store import EvidenceGraphStore
from lode.infrastructure.intake_store import PostgresIntakeStore
from lode.infrastructure.investigation_leases import InvestigationLeaseStore
from lode.infrastructure.investigation_snapshots import ConnectorSnapshotStore
from lode.infrastructure.investigation_store import PostgresInvestigationStore


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _operation(action_id: str, hypothesis_id: str = "h1") -> PlannedOperation:
    return PlannedOperation(
        action_id=action_id,
        purpose="Validate the current mechanism",
        expected_evidence="One deterministic validation artifact",
        evidence_anchors=("incident.trace_id",),
        supports_hypotheses=(hypothesis_id,),
        refutes_hypotheses=(),
        selection_reason="The operation independently tests the current evidence gap",
        stop_condition="Stop after one deterministic result",
        estimated_cost=0.0,
    )


def _capability(action_id: str) -> CapabilityEntry:
    return CapabilityEntry(
        action_id=action_id,
        operation_kind="validation",
        evidence_types=("validation_result",),
        evidence_anchors=("incident.trace_id",),
        resource_summary={"validator": action_id},
        resource_key=action_id,
        server_cost=0.0,
        timeout_ms=1_000,
        result_limit=1,
        output_bytes=10_000,
    )


def _budget() -> DecisionBudget:
    return DecisionBudget(8, 8, 1_000_000, 10.0, 60_000)


class ArtifactExecutor:
    def __init__(self, investigation_id: int) -> None:
        self.investigation_id = investigation_id
        self.calls: list[str] = []

    async def execute(self, operation_id: int, operation: PlannedOperation) -> OperationResult:
        self.calls.append(operation.action_id)
        async with AsyncSessionLocal() as session:
            payload = {"action_id": operation.action_id, "operation_id": operation_id}
            artifact = EvidenceArtifact(
                investigation_id=self.investigation_id,
                collection_id=None,
                artifact_kind="validation_result",
                evidence_class="runtime",
                content_masked=payload,
                content_hash=canonical_hash(payload),
                provenance={"operation_id": operation_id},
                source_time_start=None,
                source_time_end=None,
                source_revision=None,
                data_class="masked",
                prompt_injection_markers=[],
            )
            session.add(artifact)
            await session.flush()
            artifact_id = artifact.id
            await session.commit()
        if operation.action_id.endswith("fail"):
            return OperationResult(
                "failed",
                {},
                (),
                {"duration_ms": 2, "output_bytes": 0, "cost": 0.0},
                "fixture_failure",
                {"operation_id": operation_id},
            )
        return OperationResult(
            "succeeded",
            {"artifact_id": artifact_id},
            (artifact_id,),
            {"duration_ms": 2, "output_bytes": 32, "cost": 0.0},
        )


async def _fixture() -> tuple[int, int, int]:
    suffix = uuid4().hex[:12]
    async with AsyncSessionLocal() as session:
        user = User(
            email=f"investigation-check+{suffix}@example.invalid",
            name=f"Investigation Check {suffix}",
            role="admin",
            status="active",
        )
        workspace = Workspace(
            name=f"Investigation Check {suffix}",
            ingestion_topic=f"investigation-check-{suffix}",
        )
        session.add_all([user, workspace])
        await session.flush()
        connector = EvidenceConnector(
            workspace_id=workspace.id,
            name="check-postgresql",
            kind="postgresql",
            kind_version=1,
            config={"host": "replica.invalid", "port": 5432},
            secret_ciphertext=encrypt_value("connector-secret"),
            instance_revision=1,
            verification_status="healthy",
            verified_at=datetime.now(UTC),
            last_introspected_at=datetime.now(UTC),
            capabilities=["query"],
        )
        session.add(connector)
        await session.flush()
        session.add(
            EvidenceAccessScope(
                connector_id=connector.id,
                allowed_languages=["sql"],
                scope_config={
                    "evidence_anchors": ["incident.trace_id"],
                    "data_class": "masked",
                },
                schema_catalog={"tables": {"orders": {"columns": ["trace_id"]}}},
                schema_catalog_revision=1,
                read_policy_revision=1,
                execution_budget_policy={
                    "max_result_limit": 10,
                    "max_timeout_ms": 1_000,
                    "max_output_bytes": 10_000,
                    "max_parallel_operations": 1,
                },
                normalization_policy_revision=1,
                revision=1,
            )
        )
        await session.commit()
        request = ManualIncidentRequest.model_validate(
            {
                "workspace_id": workspace.id,
                "occurred_at": "2026-08-27T10:00:00Z",
                "severity": "WARNING",
                "event": "investigation.orchestration.check",
                "trace_id": "trace-check",
                "source_revision": "a" * 40,
                "error": {
                    "type": "Check",
                    "message": "orchestration",
                    "stack": "frame",
                    "cause": None,
                },
            }
        )
        intake = await PostgresIntakeStore(session).persist_manual(
            workspace_id=workspace.id,
            incident=normalize_manual(request),
            created_by=user.id,
        )
        return workspace.id, intake.investigation_id, connector.id


async def main() -> None:
    workspace_id, investigation_id, connector_id = await _fixture()
    snapshots = ConnectorSnapshotStore(AsyncSessionLocal)
    frozen = await snapshots.capabilities(investigation_id)
    assert len(frozen) == 1 and frozen[0].health_status == "healthy"
    async with AsyncSessionLocal() as session:
        connector = await session.get(EvidenceConnector, connector_id)
        connector.state = "disabled"
        connector.verification_status = "unavailable"
        await session.commit()
    frozen_again = await snapshots.capabilities(investigation_id)
    assert frozen_again[0].snapshot_hash == frozen[0].snapshot_hash
    assert frozen_again[0].health_status == "healthy"

    operations = (_operation("validate:success"), _operation("validate:fail"))
    decision = InvestigationDecision(
        "continue",
        (Hypothesis("h1", "The runtime state is inconsistent"),),
        operations,
        "Run two independent validation operations",
    )
    evaluated = DecisionPolicyEngine().evaluate(
        decision,
        tuple(_capability(value.action_id) for value in operations),
        budget=_budget(),
        allowed_value_refs=frozenset({"incident.trace_id"}),
    )
    store = PostgresInvestigationStore(AsyncSessionLocal)
    executor = ArtifactExecutor(investigation_id)
    coordinator = DurableWaveCoordinator(store, executor)
    first = await coordinator.execute(investigation_id, evaluated)
    assert [value.status for value in first.results] == ["succeeded", "failed"]
    calls_after_first = list(executor.calls)
    state_after_first = await store.load_state(investigation_id)
    replay = await coordinator.execute(investigation_id, evaluated)
    state_after_replay = await store.load_state(investigation_id)
    assert executor.calls == calls_after_first
    assert replay.results == first.results
    assert (
        state_after_replay.state_packet["budget_usage"]
        == state_after_first.state_packet["budget_usage"]
    )

    successful_artifact = first.results[0].evidence_refs[0]
    failed_artifact = await _artifact_for_action(investigation_id, "validate:fail")
    alpha = f"unknown:{canonical_hash({'identity': 'alpha'})[:24]}"
    beta = f"unknown:{canonical_hash({'identity': 'beta'})[:24]}"
    events = (
        NormalizedLogEvent(
            occurred_at=datetime(2026, 8, 27, 10, 0, tzinfo=UTC),
            connector_snapshot_id=frozen[0].snapshot_id,
            provider_position="1",
            raw_excerpt_masked="alpha",
            attributes_masked={},
            resource_attributes_masked={},
            trace_match={"value_hash": _sha("trace-check"), "location": "body"},
            component_candidates=({"identity": "alpha", "location": "body"},),
            relation_hints=(),
            revision_hints=(),
            provider_metadata={"fixture.position": "1"},
            evidence_artifact_id=successful_artifact,
        ),
        NormalizedLogEvent(
            occurred_at=datetime(2026, 8, 27, 10, 0, 1, tzinfo=UTC),
            connector_snapshot_id=frozen[0].snapshot_id,
            provider_position="2",
            raw_excerpt_masked="beta",
            attributes_masked={},
            resource_attributes_masked={},
            trace_match={"value_hash": _sha("trace-check"), "location": "body"},
            component_candidates=({"identity": "beta", "location": "body"},),
            relation_hints=(
                {
                    "type": "span_parent",
                    "parent_entity": alpha,
                    "child_entity": beta,
                    "parent_span_id": "span-1",
                },
            ),
            revision_hints=(),
            provider_metadata={"fixture.position": "2"},
            evidence_artifact_id=failed_artifact,
        ),
    )
    projection = EvidenceGraphProjector().project(events, aliases=())
    async with AsyncSessionLocal() as session:
        await EvidenceGraphStore(session).persist(
            workspace_id=workspace_id,
            investigation_id=investigation_id,
            projection=projection,
        )
    assert len(projection.relations) == 1

    lease_time = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)
    async with AsyncSessionLocal() as session:
        earliest = (
            await session.execute(select(func.min(InvestigationJob.available_at)))
        ).scalar_one()
        own_job = (
            await session.execute(
                select(InvestigationJob).where(
                    InvestigationJob.investigation_id == investigation_id
                )
            )
        ).scalar_one()
        own_job.available_at = earliest - timedelta(days=1)
        await session.commit()
    lease = InvestigationLeaseStore(AsyncSessionLocal, owner="worker:first", lease_ttl_seconds=30)
    claimed = await lease.claim(now=lease_time)
    assert claimed is not None and claimed.investigation_id == investigation_id
    recovery_operation = _operation("validate:recovery", "h2")
    recovery_decision = InvestigationDecision(
        "continue",
        (Hypothesis("h2", "The interrupted operation should resume"),),
        (recovery_operation,),
        "Exercise lease recovery",
    )
    recovery_evaluated = DecisionPolicyEngine().evaluate(
        recovery_decision,
        (_capability("validate:recovery"),),
        budget=_budget(),
    )
    recovery_wave = await store.prepare_wave(investigation_id, recovery_evaluated)
    await store.mark_operation_running(recovery_wave.operations[0].operation_id)
    assert await lease.reclaim_expired(now=lease_time + timedelta(seconds=31)) >= 1
    async with AsyncSessionLocal() as session:
        earliest = (
            await session.execute(select(func.min(InvestigationJob.available_at)))
        ).scalar_one()
        recovered_job = (
            await session.execute(
                select(InvestigationJob).where(
                    InvestigationJob.investigation_id == investigation_id
                )
            )
        ).scalar_one()
        assert recovered_job.status == "pending"
        recovered_job.available_at = earliest - timedelta(days=1)
        await session.commit()
    second_lease = InvestigationLeaseStore(
        AsyncSessionLocal, owner="worker:second", lease_ttl_seconds=30
    )
    reclaimed = await second_lease.claim(now=lease_time + timedelta(seconds=31))
    assert reclaimed is not None and reclaimed.investigation_id == investigation_id

    async with AsyncSessionLocal() as session:
        job = (
            await session.execute(
                select(InvestigationJob).where(
                    InvestigationJob.investigation_id == investigation_id
                )
            )
        ).scalar_one()
        run = await session.get(Investigation, investigation_id)
        recovered_operation = await session.get(
            InvestigationOperation, recovery_wave.operations[0].operation_id
        )
        recovered_step = await session.get(InvestigationStep, recovery_wave.step_id)
        counts = {
            "entities": len(
                (
                    await session.execute(
                        select(ObservedEntity.id).where(
                            ObservedEntity.investigation_id == investigation_id
                        )
                    )
                )
                .scalars()
                .all()
            ),
            "events": len(
                (
                    await session.execute(
                        select(ObservedEvent.id).where(
                            ObservedEvent.investigation_id == investigation_id
                        )
                    )
                )
                .scalars()
                .all()
            ),
            "relations": len(
                (
                    await session.execute(
                        select(ObservedRelation.id).where(
                            ObservedRelation.investigation_id == investigation_id
                        )
                    )
                )
                .scalars()
                .all()
            ),
            "resource_observations": len(
                (
                    await session.execute(
                        select(ResourceObservation.id).where(
                            ResourceObservation.workspace_id == workspace_id,
                            ResourceObservation.source_revision
                            == f"investigation:{investigation_id}",
                        )
                    )
                )
                .scalars()
                .all()
            ),
        }
        assert job.status == "running" and job.claimed_by == "worker:second"
        assert run.lease_owner == "worker:second"
        assert recovered_operation.status == "interrupted"
        assert recovered_step.status == "interrupted"
        print(
            json.dumps(
                {
                    **counts,
                    "connector_snapshot_stable": True,
                    "first_wave": [value.status for value in first.results],
                    "provider_calls_after_replay": len(executor.calls),
                    "replay_budget_stable": True,
                    "lease_reclaimed": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
    await engine.dispose()


async def _artifact_for_action(investigation_id: int, action_id: str) -> int:
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(EvidenceArtifact.id)
                .where(
                    EvidenceArtifact.investigation_id == investigation_id,
                    EvidenceArtifact.content_masked["action_id"].astext == action_id,
                )
                .order_by(EvidenceArtifact.id)
            )
        ).scalar_one()


if __name__ == "__main__":
    asyncio.run(main())
