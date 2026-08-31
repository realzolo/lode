"""Safe, deterministic read projection for the investigation execution graph."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.types import EntityId
from lode.db.models import (
    AIInvocation,
    AuthorizedEvidenceRead,
    EvidenceAccessDecision,
    EvidenceArtifact,
    EvidenceCollection,
    EvidenceConnector,
    EvidenceReadAttempt,
    GitRepository,
    Investigation,
    InvestigationConnectorSnapshot,
    InvestigationDecision,
    InvestigationInput,
    InvestigationJob,
    InvestigationOperation,
    InvestigationOperationEvent,
    InvestigationReport,
    InvestigationRepositorySnapshot,
    InvestigationStep,
    NativeReadCandidate,
)

_NATIVE_ACTION = re.compile(
    r"^native:(?P<snapshot>[1-9][0-9]*):"
    r"(?P<language>logql|elasticsearch_query_dsl|opensearch_query_dsl|sql|https|command)$"
)
_SOURCE_ACTION = re.compile(r"^source:(?P<snapshot>[1-9][0-9]*):inspect$")
_REPORT_EVIDENCE_MARKER = re.compile(
    r"\s*(?:【证据\s*#?[1-9][0-9]*】|\[(?:Evidence|证据)\s*#?[1-9][0-9]*\]|\[[1-9][0-9]*\])",
    re.IGNORECASE,
)
_RESULT_PAGE_BYTES = 256 * 1024

NodeType = Literal[
    "input",
    "decision",
    "operation",
    "synthesis",
    "verification",
    "report",
    "phase",
]
EdgeKind = Literal["sequence", "dispatch", "continue", "report"]
GraphPhase = Literal["queued", "planning", "executing", "reporting", "completed", "failed"]


class ExecutionGraphLane(BaseModel):
    id: str
    kind: Literal["control", "connector", "repository"]
    label: str
    subtitle: str | None = None
    connector_kind: str | None = None
    snapshot_id: EntityId | None = None


class ExecutionGraphStage(BaseModel):
    index: int
    kind: Literal["input", "decision", "execution", "reporting", "result"]
    ordinal: int | None = None


class ExecutionGraphNode(BaseModel):
    id: str
    node_type: NodeType
    lane_id: str
    stage_index: int
    round_ordinal: int | None = None
    status: str
    title: str
    subtitle: str | None = None
    purpose: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    evidence_count: int = 0
    evidence_refs: list[EntityId] = Field(default_factory=list)
    record_count: int | None = None
    failure_code: str | None = None
    detail_available: bool = True


class ExecutionGraphEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: EdgeKind
    status: Literal["default", "complete", "active", "failed"]


class UnusedConnector(BaseModel):
    snapshot_id: EntityId
    connector_id: EntityId
    name: str
    kind: str
    allowed_languages: list[str]
    reason_code: str | None = None


class InvestigationExecutionGraph(BaseModel):
    schema_version: Literal["investigation-execution-graph.v1"] = "investigation-execution-graph.v1"
    investigation_id: EntityId
    status: str
    phase: GraphPhase
    event_cursor: int
    active_node_ids: list[str]
    lanes: list[ExecutionGraphLane]
    stages: list[ExecutionGraphStage]
    nodes: list[ExecutionGraphNode]
    edges: list[ExecutionGraphEdge]
    unused_connectors: list[UnusedConnector]


class ExecutionArtifactSummary(BaseModel):
    id: EntityId
    kind: str
    evidence_class: str
    data_class: str
    record_count: int | None = None
    archived_at: datetime


class ExecutionArtifactPage(BaseModel):
    artifact_id: EntityId
    artifact_kind: str
    metadata: dict[str, Any]
    items: list[Any]
    total_items: int
    after_index: int
    next_after_index: int | None
    preview_bytes: int
    item_truncated: bool = False


class InvestigationExecutionNodeDetail(BaseModel):
    schema_version: Literal["investigation-execution-node.v1"] = "investigation-execution-node.v1"
    node_id: str
    node_type: NodeType
    status: str
    title: str
    overview: dict[str, Any]
    query: dict[str, Any] | None = None
    authorization: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    events: list[dict[str, Any]]
    artifacts: list[ExecutionArtifactSummary]
    result_page: ExecutionArtifactPage | None = None


class _ProjectionRows:
    def __init__(self) -> None:
        self.input: InvestigationInput | None = None
        self.job: InvestigationJob | None = None
        self.report: InvestigationReport | None = None
        self.steps: tuple[InvestigationStep, ...] = ()
        self.decisions: tuple[InvestigationDecision, ...] = ()
        self.operations: tuple[InvestigationOperation, ...] = ()
        self.events: tuple[InvestigationOperationEvent, ...] = ()
        self.connector_snapshots: tuple[tuple[InvestigationConnectorSnapshot, str], ...] = ()
        self.repository_snapshots: tuple[tuple[InvestigationRepositorySnapshot, str], ...] = ()
        self.invocations: tuple[AIInvocation, ...] = ()
        self.candidates: tuple[NativeReadCandidate, ...] = ()
        self.access_decisions: tuple[EvidenceAccessDecision, ...] = ()
        self.authorized_reads: tuple[AuthorizedEvidenceRead, ...] = ()
        self.attempts: tuple[EvidenceReadAttempt, ...] = ()
        self.collections: tuple[EvidenceCollection, ...] = ()
        self.artifacts: tuple[EvidenceArtifact, ...] = ()


async def build_execution_graph(
    session: AsyncSession, investigation: Investigation
) -> InvestigationExecutionGraph:
    rows = await _load_rows(session, investigation.id)
    graph = _GraphBuilder(investigation, rows)
    return graph.build()


async def build_node_detail(
    session: AsyncSession,
    investigation: Investigation,
    node_id: str,
) -> InvestigationExecutionNodeDetail | None:
    rows = await _load_rows(session, investigation.id)
    graph = _GraphBuilder(investigation, rows)
    node = next((item for item in graph.build().nodes if item.id == node_id), None)
    if node is None or not node.detail_available:
        return None
    return graph.detail(node)


async def build_artifact_page(
    session: AsyncSession,
    investigation: Investigation,
    node_id: str,
    artifact_id: int,
    *,
    after_index: int,
    limit: int,
) -> ExecutionArtifactPage | None:
    rows = await _load_rows(session, investigation.id)
    graph = _GraphBuilder(investigation, rows)
    node = next((item for item in graph.build().nodes if item.id == node_id), None)
    artifact = next((item for item in rows.artifacts if item.id == artifact_id), None)
    if node is None or artifact is None or artifact.id not in graph.artifact_ids(node):
        return None
    return _artifact_page(artifact, after_index=after_index, limit=limit)


async def _load_rows(session: AsyncSession, investigation_id: int) -> _ProjectionRows:
    rows = _ProjectionRows()
    rows.input = await session.get(InvestigationInput, investigation_id)
    rows.job = await session.scalar(
        select(InvestigationJob).where(InvestigationJob.investigation_id == investigation_id)
    )
    rows.report = await session.get(InvestigationReport, investigation_id)
    rows.steps = await _owned_rows(
        session, InvestigationStep, investigation_id, InvestigationStep.ordinal
    )
    rows.decisions = await _owned_rows(
        session, InvestigationDecision, investigation_id, InvestigationDecision.ordinal
    )
    rows.operations = await _owned_rows(
        session, InvestigationOperation, investigation_id, InvestigationOperation.ordinal
    )
    rows.events = await _owned_rows(
        session,
        InvestigationOperationEvent,
        investigation_id,
        InvestigationOperationEvent.sequence,
    )
    connector_values = (
        await session.execute(
            select(InvestigationConnectorSnapshot, EvidenceConnector.name)
            .join(
                EvidenceConnector,
                EvidenceConnector.id == InvestigationConnectorSnapshot.connector_id,
            )
            .where(InvestigationConnectorSnapshot.investigation_id == investigation_id)
            .order_by(InvestigationConnectorSnapshot.id)
        )
    ).all()
    rows.connector_snapshots = tuple((snapshot, str(name)) for snapshot, name in connector_values)
    repository_values = (
        await session.execute(
            select(InvestigationRepositorySnapshot, GitRepository.full_name)
            .join(GitRepository, GitRepository.id == InvestigationRepositorySnapshot.repository_id)
            .where(InvestigationRepositorySnapshot.investigation_id == investigation_id)
            .order_by(InvestigationRepositorySnapshot.priority, InvestigationRepositorySnapshot.id)
        )
    ).all()
    rows.repository_snapshots = tuple((snapshot, str(name)) for snapshot, name in repository_values)
    rows.invocations = await _owned_rows(session, AIInvocation, investigation_id, AIInvocation.id)
    rows.candidates = await _owned_rows(
        session, NativeReadCandidate, investigation_id, NativeReadCandidate.id
    )
    rows.access_decisions = await _owned_rows(
        session, EvidenceAccessDecision, investigation_id, EvidenceAccessDecision.id
    )
    rows.authorized_reads = await _owned_rows(
        session, AuthorizedEvidenceRead, investigation_id, AuthorizedEvidenceRead.id
    )
    rows.attempts = await _owned_rows(
        session, EvidenceReadAttempt, investigation_id, EvidenceReadAttempt.id
    )
    rows.collections = await _owned_rows(
        session, EvidenceCollection, investigation_id, EvidenceCollection.id
    )
    rows.artifacts = await _owned_rows(
        session, EvidenceArtifact, investigation_id, EvidenceArtifact.id
    )
    return rows


async def _owned_rows(
    session: AsyncSession, model: Any, investigation_id: int, order_by: Any
) -> tuple[Any, ...]:
    return tuple(
        (
            await session.execute(
                select(model).where(model.investigation_id == investigation_id).order_by(order_by)
            )
        )
        .scalars()
        .all()
    )


class _GraphBuilder:
    def __init__(self, investigation: Investigation, rows: _ProjectionRows) -> None:
        self.investigation = investigation
        self.rows = rows
        self.step_by_id = {item.id: item for item in rows.steps}
        self.operations_by_decision: dict[int, list[InvestigationOperation]] = defaultdict(list)
        for operation in rows.operations:
            self.operations_by_decision[operation.decision_id].append(operation)
        self.events_by_operation: dict[int, list[InvestigationOperationEvent]] = defaultdict(list)
        for event in rows.events:
            self.events_by_operation[event.operation_id].append(event)
        self.snapshot_by_id = {item.id: (item, name) for item, name in rows.connector_snapshots}
        self.repository_by_id = {item.id: (item, name) for item, name in rows.repository_snapshots}
        self.candidate_by_operation = {item.operation_id: item for item in rows.candidates}
        self.access_by_candidate = {item.candidate_id: item for item in rows.access_decisions}
        self.authorized_by_access = {
            item.access_decision_id: item for item in rows.authorized_reads
        }
        self.attempts_by_authorized: dict[int, list[EvidenceReadAttempt]] = defaultdict(list)
        for attempt in rows.attempts:
            self.attempts_by_authorized[attempt.authorized_read_id].append(attempt)
        self.collections_by_operation: dict[int, list[EvidenceCollection]] = defaultdict(list)
        for collection in rows.collections:
            if collection.operation_id is not None:
                self.collections_by_operation[collection.operation_id].append(collection)
        self.artifacts_by_collection: dict[int, list[EvidenceArtifact]] = defaultdict(list)
        for artifact in rows.artifacts:
            if artifact.collection_id is not None:
                self.artifacts_by_collection[artifact.collection_id].append(artifact)
        self.invocation_by_id = {item.id: item for item in rows.invocations}

    def build(self) -> InvestigationExecutionGraph:
        nodes: list[ExecutionGraphNode] = [self._input_node()]
        stages: list[ExecutionGraphStage] = [ExecutionGraphStage(index=0, kind="input")]
        used_lanes: set[str] = {"control"}
        decision_nodes: dict[int, ExecutionGraphNode] = {}
        operation_nodes: dict[int, ExecutionGraphNode] = {}

        for decision in self.rows.decisions:
            decision_stage = decision.ordinal * 2 - 1
            execution_stage = decision.ordinal * 2
            stages.append(
                ExecutionGraphStage(index=decision_stage, kind="decision", ordinal=decision.ordinal)
            )
            node = self._decision_node(decision, decision_stage)
            nodes.append(node)
            decision_nodes[decision.id] = node
            operations = self.operations_by_decision.get(decision.id, [])
            if operations:
                stages.append(
                    ExecutionGraphStage(
                        index=execution_stage, kind="execution", ordinal=decision.ordinal
                    )
                )
            for operation in operations:
                operation_node = self._operation_node(operation, execution_stage, decision.ordinal)
                nodes.append(operation_node)
                operation_nodes[operation.id] = operation_node
                used_lanes.add(operation_node.lane_id)

        tail_ids = self._graph_tail_ids(decision_nodes, operation_nodes)
        reporting_nodes, reporting_stages = self._reporting_nodes(
            max((item.stage_index for item in nodes), default=0) + 1
        )
        nodes.extend(reporting_nodes)
        stages.extend(reporting_stages)

        ephemeral = self._ephemeral_node(max((item.stage_index for item in nodes), default=0) + 1)
        if ephemeral is not None:
            nodes.append(ephemeral)
            stages.append(
                ExecutionGraphStage(
                    index=ephemeral.stage_index,
                    kind="reporting" if self.investigation.status == "reporting" else "decision",
                )
            )

        edges = self._edges(
            decision_nodes,
            operation_nodes,
            reporting_nodes,
            ephemeral,
            tail_ids,
            nodes,
        )
        active_node_ids = [item.id for item in nodes if item.status == "running"]
        if not active_node_ids:
            queued = [
                item for item in nodes if item.status == "queued" and item.node_type == "operation"
            ]
            if queued:
                latest_stage = max(item.stage_index for item in queued)
                active_node_ids = [item.id for item in queued if item.stage_index == latest_stage]
        if (
            not active_node_ids
            and ephemeral is not None
            and ephemeral.status in {"queued", "running"}
        ):
            active_node_ids = [ephemeral.id]
        lanes = self._lanes(used_lanes)
        return InvestigationExecutionGraph(
            investigation_id=self.investigation.id,
            status=self.investigation.status,
            phase=self._phase(active_node_ids),
            event_cursor=self.investigation.event_cursor,
            active_node_ids=active_node_ids,
            lanes=lanes,
            stages=_unique_stages(stages),
            nodes=sorted(nodes, key=lambda item: (item.stage_index, item.lane_id, item.id)),
            edges=edges,
            unused_connectors=self._unused_connectors(used_lanes),
        )

    def detail(self, node: ExecutionGraphNode) -> InvestigationExecutionNodeDetail:
        if node.node_type == "input":
            input_row = self.rows.input
            return InvestigationExecutionNodeDetail(
                node_id=node.id,
                node_type=node.node_type,
                status=node.status,
                title=node.title,
                overview={
                    "source_type": input_row.source_type if input_row else None,
                    "title": input_row.title if input_row else None,
                    "summary": input_row.summary if input_row else None,
                    "severity": input_row.severity if input_row else None,
                    "observed_at": input_row.observed_at if input_row else None,
                    "repository_binding_id": (
                        input_row.repository_binding_id if input_row else None
                    ),
                    "error": input_row.error_masked if input_row else {},
                },
                events=[],
                artifacts=self._artifact_summaries(node),
                result_page=self._first_page(node),
            )
        if node.node_type == "decision":
            decision = self._entity(node.id, "decision", self.rows.decisions)
            invocation = (
                self.invocation_by_id.get(decision.model_invocation_id)
                if decision.model_invocation_id is not None
                else None
            )
            return InvestigationExecutionNodeDetail(
                node_id=node.id,
                node_type=node.node_type,
                status=node.status,
                title=node.title,
                overview={
                    "ordinal": decision.ordinal,
                    "decision": decision.decision,
                    "hypotheses": decision.hypotheses,
                    "selected_operation_count": decision.selected_operation_count,
                    "policy_outcome": decision.policy_outcome,
                },
                execution=_invocation_summary(invocation),
                events=[],
                artifacts=[],
            )
        if node.node_type == "operation":
            operation = self._entity(node.id, "operation", self.rows.operations)
            candidate = self.candidate_by_operation.get(operation.id)
            access = self.access_by_candidate.get(candidate.id) if candidate else None
            authorized = self.authorized_by_access.get(access.id) if access else None
            attempts = self.attempts_by_authorized.get(authorized.id, []) if authorized else []
            artifacts = self._artifact_summaries(node)
            return InvestigationExecutionNodeDetail(
                node_id=node.id,
                node_type=node.node_type,
                status=node.status,
                title=node.title,
                overview={
                    "operation_kind": operation.operation_kind,
                    "purpose": operation.purpose,
                    "expected_evidence": operation.expected_evidence,
                    "selection_reason": operation.selection_reason,
                    "stop_condition": operation.stop_condition,
                    "evidence_anchors": operation.evidence_anchors,
                    "round_ordinal": self.step_by_id[operation.step_id].ordinal,
                    "input_masked": operation.input_masked,
                },
                query=_query_detail(candidate, access, attempts),
                authorization=_access_summary(access),
                execution={
                    "metrics": operation.metrics,
                    "result": operation.result_masked,
                    "failure_code": operation.failure_code,
                    "failure_detail": operation.failure_detail,
                    "started_at": operation.started_at,
                    "finished_at": operation.finished_at,
                    "attempts": [_attempt_summary(item) for item in attempts],
                },
                events=[_event_summary(item) for item in self.events_by_operation[operation.id]],
                artifacts=artifacts,
                result_page=self._first_page(node),
            )
        if node.node_type in {"synthesis", "verification"}:
            invocation_id = int(node.id.split(":", 1)[1])
            invocation = self.invocation_by_id[invocation_id]
            overview: dict[str, Any] = {"role": invocation.role}
            if invocation.role == "verifier" and isinstance(invocation.output_masked, dict):
                verdict = invocation.output_masked.get("verdict")
                reasons = invocation.output_masked.get("reasons")
                if isinstance(verdict, str):
                    overview["verdict"] = verdict
                if isinstance(reasons, list):
                    overview["reasons"] = [item for item in reasons if isinstance(item, str)][:20]
            return InvestigationExecutionNodeDetail(
                node_id=node.id,
                node_type=node.node_type,
                status=node.status,
                title=node.title,
                overview=overview,
                execution=_invocation_summary(invocation),
                events=[],
                artifacts=[],
            )
        report = self.rows.report
        return InvestigationExecutionNodeDetail(
            node_id=node.id,
            node_type=node.node_type,
            status=node.status,
            title=node.title,
            overview={
                "result_state": report.result_state if report else self.investigation.result_state,
                "headline": _clean_report_text(report.headline) if report else None,
                "executive_summary": (
                    _clean_report_text(report.executive_summary) if report else None
                ),
                "impact_scope": report.impact_scope if report else [],
                "causal_graph": report.causal_graph if report else None,
                "evidence_gaps": report.evidence_gaps if report else [],
                "action_recommendations": report.action_recommendations if report else [],
            },
            events=[],
            artifacts=[],
        )

    def artifact_ids(self, node: ExecutionGraphNode) -> set[int]:
        if node.node_type == "operation":
            operation_id = int(node.id.split(":", 1)[1])
            return {
                artifact.id
                for collection in self.collections_by_operation.get(operation_id, [])
                for artifact in self.artifacts_by_collection.get(collection.id, [])
            }
        if node.node_type == "input":
            return {
                artifact.id
                for artifact in self.rows.artifacts
                if artifact.artifact_kind == "incident_input"
            }
        return set()

    def _input_node(self) -> ExecutionGraphNode:
        input_row = self.rows.input
        evidence_refs = sorted(self.artifact_ids_for_input())
        return ExecutionGraphNode(
            id=f"input:{self.investigation.id}",
            node_type="input",
            lane_id="control",
            stage_index=0,
            status="succeeded" if input_row is not None else "queued",
            title=input_row.title if input_row is not None else "Incident input",
            subtitle=input_row.severity if input_row is not None else None,
            purpose="Immutable incident input",
            started_at=input_row.created_at
            if input_row is not None
            else self.investigation.created_at,
            finished_at=input_row.created_at if input_row is not None else None,
            evidence_count=len(evidence_refs),
            evidence_refs=evidence_refs,
        )

    def artifact_ids_for_input(self) -> set[int]:
        return {
            artifact.id
            for artifact in self.rows.artifacts
            if artifact.artifact_kind == "incident_input"
        }

    def _decision_node(
        self, decision: InvestigationDecision, stage_index: int
    ) -> ExecutionGraphNode:
        invocation = (
            self.invocation_by_id.get(decision.model_invocation_id)
            if decision.model_invocation_id is not None
            else None
        )
        status = "succeeded" if decision.policy_outcome != "reject" else "rejected"
        return ExecutionGraphNode(
            id=f"decision:{decision.id}",
            node_type="decision",
            lane_id="control",
            stage_index=stage_index,
            round_ordinal=decision.ordinal,
            status=status,
            title=f"Decision {decision.ordinal}",
            subtitle=decision.decision,
            purpose=(
                self.step_by_id[decision.step_id].objective
                if decision.step_id in self.step_by_id
                else None
            ),
            started_at=invocation.created_at if invocation is not None else decision.created_at,
            finished_at=decision.created_at,
            duration_ms=invocation.latency_ms if invocation is not None else None,
            failure_code=(
                next(
                    (
                        str(item.get("code"))
                        for item in decision.policy_decisions
                        if isinstance(item, dict) and item.get("outcome") == "reject"
                    ),
                    None,
                )
            ),
        )

    def _operation_node(
        self, operation: InvestigationOperation, stage_index: int, round_ordinal: int
    ) -> ExecutionGraphNode:
        lane_id, title, subtitle = self._operation_identity(operation)
        artifacts = [
            artifact
            for collection in self.collections_by_operation.get(operation.id, [])
            for artifact in self.artifacts_by_collection.get(collection.id, [])
        ]
        record_count = sum(_artifact_record_count(artifact) or 0 for artifact in artifacts)
        return ExecutionGraphNode(
            id=f"operation:{operation.id}",
            node_type="operation",
            lane_id=lane_id,
            stage_index=stage_index,
            round_ordinal=round_ordinal,
            status=operation.status,
            title=title,
            subtitle=subtitle,
            purpose=operation.purpose,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
            duration_ms=_duration_ms(operation.started_at, operation.finished_at),
            evidence_count=len(artifacts),
            evidence_refs=sorted(artifact.id for artifact in artifacts),
            record_count=record_count if artifacts else None,
            failure_code=operation.failure_code,
        )

    def _operation_identity(self, operation: InvestigationOperation) -> tuple[str, str, str]:
        native = _NATIVE_ACTION.fullmatch(operation.action_id)
        if native is not None:
            snapshot_id = int(native.group("snapshot"))
            snapshot = self.snapshot_by_id.get(snapshot_id)
            if snapshot is not None:
                return (
                    f"connector:{snapshot_id}",
                    snapshot[1],
                    f"{snapshot[0].connector_kind}:{native.group('language')}",
                )
        source = _SOURCE_ACTION.fullmatch(operation.action_id)
        if source is not None:
            snapshot_id = int(source.group("snapshot"))
            snapshot = self.repository_by_id.get(snapshot_id)
            if snapshot is not None:
                return f"repository:{snapshot_id}", snapshot[1], "source_read"
        return "control", operation.purpose, operation.operation_kind

    def _reporting_nodes(
        self, first_stage: int
    ) -> tuple[list[ExecutionGraphNode], list[ExecutionGraphStage]]:
        report = self.rows.report
        nodes: list[ExecutionGraphNode] = []
        stages: list[ExecutionGraphStage] = []
        synthesis_id = report.synthesizer_invocation_id if report is not None else None
        verification_id = report.verifier_invocation_id if report is not None else None
        if synthesis_id is None:
            synthesis_id = next(
                (item.id for item in reversed(self.rows.invocations) if item.role == "synthesizer"),
                None,
            )
        if verification_id is None:
            verification_id = next(
                (item.id for item in reversed(self.rows.invocations) if item.role == "verifier"),
                None,
            )
        stage = first_stage
        for node_type, invocation_id in (
            ("synthesis", synthesis_id),
            ("verification", verification_id),
        ):
            if invocation_id is None:
                continue
            invocation = self.invocation_by_id.get(invocation_id)
            if invocation is None:
                continue
            nodes.append(
                ExecutionGraphNode(
                    id=f"{node_type}:{invocation.id}",
                    node_type=node_type,
                    lane_id="control",
                    stage_index=stage,
                    status=invocation.status,
                    title=node_type,
                    subtitle=invocation.execution_class,
                    started_at=invocation.created_at,
                    finished_at=invocation.created_at,
                    duration_ms=invocation.latency_ms,
                    failure_code=invocation.error_code,
                )
            )
            stages.append(ExecutionGraphStage(index=stage, kind="reporting"))
            stage += 1
        if report is not None:
            nodes.append(
                ExecutionGraphNode(
                    id=f"report:{self.investigation.id}",
                    node_type="report",
                    lane_id="control",
                    stage_index=stage,
                    status="succeeded" if self.investigation.status == "completed" else "running",
                    title=_clean_report_text(report.headline),
                    subtitle=report.result_state,
                    purpose=_clean_report_text(report.executive_summary),
                    started_at=report.created_at,
                    finished_at=report.published_at,
                )
            )
            stages.append(ExecutionGraphStage(index=stage, kind="result"))
        return nodes, stages

    def _ephemeral_node(self, stage_index: int) -> ExecutionGraphNode | None:
        if self.investigation.status in {"completed"} or self.rows.report is not None:
            return None
        if any(operation.status == "running" for operation in self.rows.operations):
            return None
        if any(operation.status == "queued" for operation in self.rows.operations):
            return None
        if self.investigation.status == "queued":
            phase, title = "queued", "Waiting to start"
        elif self.investigation.status == "reporting" or (
            self.rows.job is not None and self.rows.job.phase == "reporting"
        ):
            phase, title = "reporting", "Building report"
        elif self.investigation.status == "failed":
            phase, title = "failed", "Investigation failed"
        else:
            phase, title = "planning", "Planning next step"
        return ExecutionGraphNode(
            id=f"phase:{phase}",
            node_type="phase",
            lane_id="control",
            stage_index=stage_index,
            status="failed"
            if phase == "failed"
            else ("queued" if phase == "queued" else "running"),
            title=title,
            detail_available=False,
            failure_code=self.rows.job.last_error_code if self.rows.job is not None else None,
        )

    def _graph_tail_ids(
        self,
        decision_nodes: dict[int, ExecutionGraphNode],
        operation_nodes: dict[int, ExecutionGraphNode],
    ) -> list[str]:
        if not self.rows.decisions:
            return [f"input:{self.investigation.id}"]
        last_decision = self.rows.decisions[-1]
        operations = self.operations_by_decision.get(last_decision.id, [])
        if operations:
            return [operation_nodes[item.id].id for item in operations]
        return [decision_nodes[last_decision.id].id]

    def _edges(
        self,
        decision_nodes: dict[int, ExecutionGraphNode],
        operation_nodes: dict[int, ExecutionGraphNode],
        reporting_nodes: list[ExecutionGraphNode],
        ephemeral: ExecutionGraphNode | None,
        tail_ids: list[str],
        nodes: list[ExecutionGraphNode],
    ) -> list[ExecutionGraphEdge]:
        pairs: list[tuple[str, str, EdgeKind]] = []
        previous = [f"input:{self.investigation.id}"]
        for decision in self.rows.decisions:
            decision_id = decision_nodes[decision.id].id
            pairs.extend((source, decision_id, "continue") for source in previous)
            operations = self.operations_by_decision.get(decision.id, [])
            if operations:
                pairs.extend(
                    (decision_id, operation_nodes[item.id].id, "dispatch") for item in operations
                )
                previous = [operation_nodes[item.id].id for item in operations]
            else:
                previous = [decision_id]
        reporting_chain = [item.id for item in reporting_nodes]
        if reporting_chain:
            pairs.extend((source, reporting_chain[0], "report") for source in tail_ids)
            pairs.extend((source, target, "report") for source, target in pairwise(reporting_chain))
        elif ephemeral is not None:
            pairs.extend((source, ephemeral.id, "sequence") for source in tail_ids)
        return [
            ExecutionGraphEdge(
                id=f"{source}->{target}",
                source=source,
                target=target,
                kind=kind,
                status=self._edge_status(target, nodes),
            )
            for source, target, kind in pairs
            if source != target
        ]

    def _edge_status(
        self, target: str, nodes: list[ExecutionGraphNode]
    ) -> Literal["default", "complete", "active", "failed"]:
        node = next((item for item in nodes if item.id == target), None)
        if node is None:
            return "default"
        if node.status == "running":
            return "active"
        if node.status in {"failed", "rejected", "interrupted"}:
            return "failed"
        if node.status in {"succeeded", "completed"}:
            return "complete"
        return "default"

    def _lanes(self, used_lanes: set[str]) -> list[ExecutionGraphLane]:
        lanes = [ExecutionGraphLane(id="control", kind="control", label="Lode")]
        for snapshot, name in self.rows.connector_snapshots:
            lane_id = f"connector:{snapshot.id}"
            if lane_id in used_lanes:
                lanes.append(
                    ExecutionGraphLane(
                        id=lane_id,
                        kind="connector",
                        label=name,
                        subtitle=", ".join(snapshot.allowed_languages),
                        connector_kind=snapshot.connector_kind,
                        snapshot_id=snapshot.id,
                    )
                )
        for snapshot, name in self.rows.repository_snapshots:
            lane_id = f"repository:{snapshot.id}"
            if lane_id in used_lanes:
                lanes.append(
                    ExecutionGraphLane(
                        id=lane_id,
                        kind="repository",
                        label=name,
                        subtitle=snapshot.revision_policy,
                        snapshot_id=snapshot.id,
                    )
                )
        return lanes

    def _unused_connectors(self, used_lanes: set[str]) -> list[UnusedConnector]:
        rejection_by_action: dict[str, str] = {}
        for decision in self.rows.decisions:
            for value in decision.policy_decisions:
                if not isinstance(value, dict):
                    continue
                action_id = value.get("action_id")
                code = value.get("code")
                if isinstance(action_id, str) and isinstance(code, str):
                    rejection_by_action[action_id] = code
        values: list[UnusedConnector] = []
        for snapshot, name in self.rows.connector_snapshots:
            if f"connector:{snapshot.id}" in used_lanes:
                continue
            reason = next(
                (
                    rejection_by_action.get(f"native:{snapshot.id}:{language}")
                    for language in snapshot.allowed_languages
                    if rejection_by_action.get(f"native:{snapshot.id}:{language}")
                ),
                None,
            )
            values.append(
                UnusedConnector(
                    snapshot_id=snapshot.id,
                    connector_id=snapshot.connector_id,
                    name=name,
                    kind=snapshot.connector_kind,
                    allowed_languages=list(snapshot.allowed_languages),
                    reason_code=reason,
                )
            )
        return values

    def _phase(self, active_node_ids: list[str]) -> GraphPhase:
        if self.investigation.status == "completed":
            return cast(GraphPhase, "completed")
        if self.investigation.status == "failed":
            return cast(GraphPhase, "failed")
        if self.investigation.status == "queued":
            return cast(GraphPhase, "queued")
        if any(value.startswith("operation:") for value in active_node_ids) or any(
            item.status in {"queued", "running"} for item in self.rows.operations
        ):
            return cast(GraphPhase, "executing")
        if self.investigation.status == "reporting" or (
            self.rows.job is not None and self.rows.job.phase == "reporting"
        ):
            return cast(GraphPhase, "reporting")
        return cast(GraphPhase, "planning")

    def _entity(self, node_id: str, prefix: str, values: tuple[Any, ...]) -> Any:
        entity_id = int(node_id.split(":", 1)[1])
        return next(item for item in values if item.id == entity_id)

    def _artifact_summaries(self, node: ExecutionGraphNode) -> list[ExecutionArtifactSummary]:
        allowed = self.artifact_ids(node)
        return [
            ExecutionArtifactSummary(
                id=item.id,
                kind=item.artifact_kind,
                evidence_class=item.evidence_class,
                data_class=item.data_class,
                record_count=_artifact_record_count(item),
                archived_at=item.archived_at,
            )
            for item in self.rows.artifacts
            if item.id in allowed
        ]

    def _first_page(self, node: ExecutionGraphNode) -> ExecutionArtifactPage | None:
        artifact_id = next(iter(sorted(self.artifact_ids(node))), None)
        if artifact_id is None:
            return None
        artifact = next(item for item in self.rows.artifacts if item.id == artifact_id)
        return _artifact_page(artifact, after_index=0, limit=100)


def _query_detail(
    candidate: NativeReadCandidate | None,
    access: EvidenceAccessDecision | None,
    attempts: list[EvidenceReadAttempt],
) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "language": candidate.language,
        "state": (
            "executed"
            if attempts
            else "authorized"
            if access is not None and access.outcome == "allow"
            else "proposed"
        ),
        "proposed_payload": candidate.payload_masked,
        "effective_action": access.effective_action_masked if access is not None else None,
        "requested_window": candidate.requested_window,
        "requested_limit": candidate.requested_limit,
        "requested_timeout_ms": candidate.requested_timeout_ms,
    }


def _access_summary(access: EvidenceAccessDecision | None) -> dict[str, Any] | None:
    if access is None:
        return None
    return {
        "outcome": access.outcome,
        "parser_name": access.parser_name,
        "parser_version": access.parser_version,
        "policy_version": access.policy_version,
        "validation_decisions": access.validation_decisions,
        "effective_budget": access.effective_budget,
        "constraint_diff": access.constraint_diff,
        "rejection_code": access.rejection_code,
        "rejection_detail": access.rejection_detail,
    }


def _attempt_summary(attempt: EvidenceReadAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "attempt": attempt.attempt,
        "status": attempt.status,
        "preflight": attempt.preflight,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "metrics": attempt.metrics,
        "failure_code": attempt.failure_code,
        "failure_detail": attempt.failure_detail,
        "result_artifact_refs": attempt.result_artifact_refs,
    }


def _event_summary(event: InvestigationOperationEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "event_name": event.event_name,
        "message": event.message,
        "detail": event.detail_masked,
        "evidence_refs": event.evidence_refs,
        "occurred_at": event.occurred_at,
    }


def _invocation_summary(invocation: AIInvocation | None) -> dict[str, Any] | None:
    if invocation is None:
        return None
    return {
        "role": invocation.role,
        "status": invocation.status,
        "execution_class": invocation.execution_class,
        "attempt_count": invocation.attempt_count,
        "latency_ms": invocation.latency_ms,
        "termination_reason": invocation.termination_reason,
        "error_code": invocation.error_code,
        "created_at": invocation.created_at,
    }


def _clean_report_text(value: str) -> str:
    return re.sub(r"\s+", " ", _REPORT_EVIDENCE_MARKER.sub("", value)).strip()


def _artifact_record_count(artifact: EvidenceArtifact) -> int | None:
    value = artifact.content_masked.get("record_count")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    records = artifact.content_masked.get("records")
    return len(records) if isinstance(records, list) else None


def _artifact_page(
    artifact: EvidenceArtifact, *, after_index: int, limit: int
) -> ExecutionArtifactPage:
    content = artifact.content_masked
    records = content.get("records") if isinstance(content, dict) else None
    if isinstance(records, list):
        items = records
        metadata = {key: value for key, value in content.items() if key != "records"}
    else:
        items = [content]
        metadata = {}
    metadata, metadata_truncated = _bounded_json_value(metadata, byte_limit=_RESULT_PAGE_BYTES // 4)
    selected: list[Any] = []
    item_truncated = metadata_truncated
    consumed = 0
    for item in items[after_index : after_index + limit]:
        candidate_items = [*selected, item]
        if (
            _page_payload_size(
                artifact,
                metadata=metadata,
                items=candidate_items,
                total_items=len(items),
                after_index=after_index,
                next_after_index=after_index + len(candidate_items),
                item_truncated=item_truncated,
            )
            > _RESULT_PAGE_BYTES - 64
        ):
            if not selected:
                bounded, _ = _bounded_json_value(
                    item,
                    byte_limit=_RESULT_PAGE_BYTES // 2,
                )
                selected.append(bounded)
                consumed = 1
                item_truncated = True
            break
        selected.append(item)
        consumed += 1
    next_index = after_index + consumed
    page = ExecutionArtifactPage(
        artifact_id=artifact.id,
        artifact_kind=artifact.artifact_kind,
        metadata=metadata,
        items=selected,
        total_items=len(items),
        after_index=after_index,
        next_after_index=next_index if next_index < len(items) else None,
        preview_bytes=0,
        item_truncated=item_truncated,
    )
    for _ in range(3):
        page.preview_bytes = len(page.model_dump_json().encode("utf-8"))
    return page


def _page_payload_size(
    artifact: EvidenceArtifact,
    *,
    metadata: dict[str, Any],
    items: list[Any],
    total_items: int,
    after_index: int,
    next_after_index: int | None,
    item_truncated: bool,
) -> int:
    value = ExecutionArtifactPage(
        artifact_id=artifact.id,
        artifact_kind=artifact.artifact_kind,
        metadata=metadata,
        items=items,
        total_items=total_items,
        after_index=after_index,
        next_after_index=next_after_index,
        preview_bytes=0,
        item_truncated=item_truncated,
    )
    return len(value.model_dump_json().encode("utf-8"))


def _bounded_json_value(value: Any, *, byte_limit: int) -> tuple[Any, bool]:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= byte_limit:
        return value, False
    preview_limit = max(0, byte_limit - 64)
    return {
        "preview": encoded[:preview_limit].decode("utf-8", errors="ignore"),
        "truncated": True,
    }, True


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return max(0, int((finished_at - started_at).total_seconds() * 1_000))


def _unique_stages(values: list[ExecutionGraphStage]) -> list[ExecutionGraphStage]:
    unique: dict[int, ExecutionGraphStage] = {}
    for value in values:
        unique.setdefault(value.index, value)
    return [unique[index] for index in sorted(unique)]
