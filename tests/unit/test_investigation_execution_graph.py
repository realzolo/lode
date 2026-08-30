from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from lode.api.investigation_execution_graph import _artifact_page, _GraphBuilder, _ProjectionRows
from lode.api.routes.investigations import _report_summary
from lode.db.models import EvidenceArtifact


def _artifact(content: dict) -> EvidenceArtifact:
    return EvidenceArtifact(
        id=7,
        investigation_id=1,
        collection_id=2,
        artifact_kind="native_read_result",
        evidence_class="database_row",
        content_masked=content,
        content_hash="a" * 64,
        provenance={},
        data_class="internal",
        prompt_injection_markers=[],
        archived_at=datetime.now(UTC),
    )


def test_artifact_page_enforces_record_and_response_byte_limits() -> None:
    artifact = _artifact(
        {
            "record_count": 140,
            "columns": ["id", "message"],
            "records": [
                {"id": index, "message": f"row-{index}-" + "x" * 5_000}
                for index in range(140)
            ],
        }
    )

    first = _artifact_page(artifact, after_index=0, limit=100)

    assert 0 < len(first.items) <= 100
    assert first.next_after_index == len(first.items)
    assert first.preview_bytes == len(first.model_dump_json().encode("utf-8"))
    assert first.preview_bytes <= 256 * 1024

    second = _artifact_page(
        artifact,
        after_index=first.next_after_index or 0,
        limit=100,
    )
    assert second.after_index == first.next_after_index
    assert second.items[0]["id"] == first.next_after_index


def test_artifact_page_bounds_large_metadata_and_single_records() -> None:
    page = _artifact_page(
        _artifact(
            {
                "aggregation": "m" * 400_000,
                "records": [{"message": "r" * 400_000}],
            }
        ),
        after_index=0,
        limit=100,
    )

    assert page.metadata["truncated"] is True
    assert page.item_truncated is True
    assert page.items[0]["truncated"] is True
    assert page.preview_bytes <= 256 * 1024


def _investigation(status: str):
    return SimpleNamespace(
        id=1,
        status=status,
        result_state="pending",
        event_cursor=9,
        created_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("status", "job_phase", "expected", "active"),
    [
        ("queued", "investigation", "queued", ["phase:queued"]),
        ("running", "investigation", "planning", ["phase:planning"]),
        ("reporting", "reporting", "reporting", ["phase:reporting"]),
        ("completed", "reporting", "completed", []),
        ("failed", "investigation", "failed", []),
    ],
)
def test_phase_projection_uses_persisted_job_facts(
    status: str,
    job_phase: str,
    expected: str,
    active: list[str],
) -> None:
    rows = _ProjectionRows()
    rows.job = SimpleNamespace(phase=job_phase, last_error_code="worker_failed")

    graph = _GraphBuilder(_investigation(status), rows).build()

    assert graph.phase == expected
    assert graph.active_node_ids == active


def test_graph_preserves_parallel_operations_and_repeated_connector_calls() -> None:
    now = datetime.now(UTC)
    rows = _ProjectionRows()
    rows.job = SimpleNamespace(phase="reporting", last_error_code=None)
    rows.steps = (
        SimpleNamespace(id=11, ordinal=1, objective="Compare runtime evidence"),
        SimpleNamespace(id=12, ordinal=2, objective="Confirm the database finding"),
    )
    rows.decisions = (
        SimpleNamespace(
            id=21,
            step_id=11,
            ordinal=1,
            decision="continue",
            policy_outcome="allow",
            policy_decisions=[],
            selected_operation_count=2,
            hypotheses=[],
            model_invocation_id=None,
            created_at=now,
        ),
        SimpleNamespace(
            id=22,
            step_id=12,
            ordinal=2,
            decision="continue",
            policy_outcome="allow",
            policy_decisions=[],
            selected_operation_count=1,
            hypotheses=[],
            model_invocation_id=None,
            created_at=now,
        ),
    )

    def operation(
        entity_id: int,
        *,
        step_id: int,
        decision_id: int,
        action_id: str,
        status: str,
        ordinal: int,
    ):
        return SimpleNamespace(
            id=entity_id,
            step_id=step_id,
            decision_id=decision_id,
            action_id=action_id,
            operation_kind="native_read",
            purpose="Collect evidence",
            expected_evidence="masked records",
            evidence_anchors=["incident"],
            selection_reason="test the hypothesis",
            stop_condition="enough evidence",
            input_masked={},
            status=status,
            result_masked={},
            metrics={},
            failure_code=None,
            failure_detail=None,
            started_at=now,
            finished_at=now if status == "succeeded" else None,
            ordinal=ordinal,
        )

    rows.operations = (
        operation(101, step_id=11, decision_id=21, action_id="native:31:sql", status="succeeded", ordinal=1),
        operation(102, step_id=11, decision_id=21, action_id="native:32:logql", status="succeeded", ordinal=2),
        operation(103, step_id=12, decision_id=22, action_id="native:31:sql", status="running", ordinal=3),
    )
    rows.connector_snapshots = (
        (SimpleNamespace(id=31, connector_id=301, connector_kind="postgresql", allowed_languages=["sql"]), "Primary DB"),
        (SimpleNamespace(id=32, connector_id=302, connector_kind="loki", allowed_languages=["logql"]), "Runtime logs"),
        (SimpleNamespace(id=33, connector_id=303, connector_kind="https", allowed_languages=["https"]), "Status API"),
    )
    rows.collections = (SimpleNamespace(id=501, operation_id=101),)
    rows.artifacts = (
        SimpleNamespace(
            id=702,
            collection_id=501,
            artifact_kind="normalized_sql_result",
            content_masked={"record_count": 1},
        ),
        SimpleNamespace(
            id=701,
            collection_id=501,
            artifact_kind="normalized_sql_result",
            content_masked={"records": [{"id": 1}, {"id": 2}]},
        ),
    )

    graph = _GraphBuilder(_investigation("reporting"), rows).build()
    nodes = {node.id: node for node in graph.nodes}
    edge_pairs = {(edge.source, edge.target) for edge in graph.edges}

    assert graph.phase == "executing"
    assert graph.active_node_ids == ["operation:103"]
    assert nodes["operation:101"].stage_index == nodes["operation:102"].stage_index
    assert nodes["operation:101"].lane_id == nodes["operation:103"].lane_id == "connector:31"
    assert nodes["operation:101"].evidence_refs == [701, 702]
    assert nodes["operation:101"].evidence_count == 2
    assert nodes["operation:101"].record_count == 3
    assert ("operation:101", "operation:102") not in edge_pairs
    assert ("operation:102", "operation:101") not in edge_pairs
    assert {connector.snapshot_id for connector in graph.unused_connectors} == {33}


def test_report_summary_projects_deterministic_section_evidence_links() -> None:
    report = SimpleNamespace(
        headline="Database timeout confirmed",
        summary="The primary database exceeded its timeout budget. [Evidence 9]",
        incident_cause={
            "status": "confirmed",
            "mechanism": "Database timeout 【证据 9】",
            "causal_chain": ["Pool saturation [9]", "Request timeout [Evidence #7]"],
            "evidence_refs": [9, 7, 9, 0, True],
        },
        code_diagnosis={
            "status": "hypothesis",
            "summary": "Retry handling may amplify the failure.",
            "finding_refs": [44],
        },
        confirmed_facts=[
            {"text": "The query exceeded 5 seconds. [Evidence 8]", "evidence_refs": [8, 7]},
            {"text": "The pool was saturated.", "evidence_refs": [9]},
        ],
        evidence_gaps=[],
        next_step="Inspect pool sizing.",
    )
    finding = SimpleNamespace(
        id=44,
        source_artifact_id=10,
        incident_evidence_refs=[9],
        supporting_evidence_refs=[11, 10],
        counter_evidence_refs=[12],
    )

    summary = _report_summary(report, (finding,))

    assert summary is not None
    assert summary.summary == "The primary database exceeded its timeout budget."
    assert summary.cause.summary == "Database timeout"
    assert summary.cause.causal_chain == ["Pool saturation", "Request timeout"]
    assert summary.cause.evidence_refs == [7, 9]
    assert [item.model_dump() for item in summary.confirmed_facts] == [
        {"text": "The query exceeded 5 seconds.", "evidence_refs": [7, 8]},
        {"text": "The pool was saturated.", "evidence_refs": [9]},
    ]
    assert summary.code_diagnosis.evidence_refs == [9, 10, 11, 12]


def test_model_node_detail_exposes_only_product_summary() -> None:
    now = datetime.now(UTC)
    rows = _ProjectionRows()
    rows.job = SimpleNamespace(phase="reporting", last_error_code=None)
    rows.invocations = (
        SimpleNamespace(
            id=81,
            role="verifier",
            status="succeeded",
            execution_class="reasoning_optimized",
            attempt_count=1,
            latency_ms=230,
            termination_reason="stop",
            error_code=None,
            error_detail={"provider_payload": "hidden"},
            created_at=now,
            output_masked={
                "verdict": "approved",
                "reasons": ["Runtime and source evidence agree."],
                "finding_verdicts": [{"finding_index": 0, "verdict": "approved"}],
            },
            input_tokens=1_000,
            output_tokens=200,
            cost=1.5,
        ),
    )

    builder = _GraphBuilder(_investigation("reporting"), rows)
    node = next(item for item in builder.build().nodes if item.id == "verification:81")
    detail = builder.detail(node)
    serialized = detail.model_dump_json()

    assert detail.overview == {
        "role": "verifier",
        "verdict": "approved",
        "reasons": ["Runtime and source evidence agree."],
    }
    assert detail.execution == {
        "role": "verifier",
        "status": "succeeded",
        "execution_class": "reasoning_optimized",
        "attempt_count": 1,
        "latency_ms": 230,
        "termination_reason": "stop",
        "error_code": None,
        "created_at": now,
    }
    assert "input_tokens" not in serialized
    assert "output_tokens" not in serialized
    assert "cost" not in serialized
    assert "provider_payload" not in serialized
    assert "finding_verdicts" not in serialized
