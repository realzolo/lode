"""Investigation ORM tests."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from lode.db import models  # noqa: F401
from lode.db.base import Base


def _check_sql(table_name: str) -> str:
    table = Base.metadata.tables[table_name]
    return " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


def test_snapshot_tables_use_relational_rows_and_revision_hashes() -> None:
    snapshot_tables = {
        "investigation_repository_snapshots",
        "investigation_build_unit_snapshots",
        "investigation_component_snapshots",
        "investigation_connector_snapshots",
        "investigation_resource_graph_snapshots",
        "investigation_descriptor_snapshots",
        "investigation_model_policy_snapshots",
        "investigation_model_binding_snapshots",
    }
    assert snapshot_tables.issubset(Base.metadata.tables)
    for table_name in snapshot_tables:
        columns = set(Base.metadata.tables[table_name].c.keys())
        assert "investigation_id" in columns
        assert "snapshot_hash" in columns


def test_only_one_running_wave_is_allowed_per_investigation() -> None:
    step = Base.metadata.tables["investigation_steps"]
    running = next(index for index in step.indexes if index.name == "uq_investigation_step_running")

    assert running.unique
    assert running.dialect_options["postgresql"]["where"] is not None


def test_wave_operation_ordinal_enforces_four_operation_ceiling() -> None:
    operation = Base.metadata.tables["investigation_operations"]
    sql = _check_sql("investigation_operations")
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in operation.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "wave_ordinal BETWEEN 1 AND 4" in sql
    assert ("step_id", "wave_ordinal") in unique_columns
    assert ("investigation_id", "fingerprint") in unique_columns


def test_decision_contract_enforces_finish_zero_and_continue_one_to_four() -> None:
    sql = _check_sql("investigation_decisions")

    assert "selected_operation_count BETWEEN 0 AND 4" in sql
    assert "decision = 'finish' AND selected_operation_count = 0" in sql
    assert "decision = 'continue' AND selected_operation_count BETWEEN 1 AND 4" in sql


def test_operation_event_sequence_is_investigation_monotonic() -> None:
    event = Base.metadata.tables["investigation_operation_events"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in event.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("investigation_id", "sequence") in unique_columns


def test_native_read_audit_chain_has_no_direct_candidate_execution_reference() -> None:
    candidate = Base.metadata.tables["native_read_candidates"]
    decision = Base.metadata.tables["evidence_access_decisions"]
    authorized = Base.metadata.tables["authorized_evidence_reads"]
    attempt = Base.metadata.tables["evidence_read_attempts"]

    assert "authorized_read_id" not in candidate.c
    assert "candidate_id" in decision.c
    assert "access_decision_id" in authorized.c
    assert "authorized_read_id" in attempt.c
    assert "candidate_id" not in attempt.c


def test_access_decision_allow_and_reject_payloads_are_mutually_exclusive() -> None:
    sql = _check_sql("evidence_access_decisions")

    assert "outcome = 'allow'" in sql
    assert "effective_action_masked IS NOT NULL" in sql
    assert "outcome = 'reject'" in sql
    assert "rejection_code IS NOT NULL" in sql


def test_authorized_read_is_hash_bound_expiring_and_unique() -> None:
    table = Base.metadata.tables["authorized_evidence_reads"]
    sql = _check_sql("authorized_evidence_reads")
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "expires_at > issued_at" in sql
    assert ("investigation_id", "fingerprint") in unique_columns
    assert any(columns == ("token_hash",) for columns in unique_columns)


def test_read_attempt_is_written_once_in_a_terminal_state() -> None:
    table = Base.metadata.tables["evidence_read_attempts"]
    sql = _check_sql("evidence_read_attempts")

    assert not table.c.finished_at.nullable
    assert "'running'" not in sql
    assert "'succeeded'" in sql
    assert "failure_code IS NOT NULL" in sql


def test_causal_relations_require_evidence_refs() -> None:
    sql = _check_sql("observed_relations")
    assert "called" in sql
    assert "caused_by" in sql
    assert "cardinality(evidence_refs) > 0" in sql


def test_timeline_has_stable_provider_ordering_index() -> None:
    event = Base.metadata.tables["observed_events"]
    index = next(index for index in event.indexes if index.name == "ix_observed_events_timeline")
    assert tuple(column.name for column in index.columns) == (
        "investigation_id",
        "occurred_at",
        "connector_snapshot_id",
        "provider_position",
    )


def test_model_invocation_always_references_route_and_context_bundle() -> None:
    invocation = Base.metadata.tables["ai_invocations"]

    assert not invocation.c.routing_decision_id.nullable
    assert not invocation.c.context_bundle_revision_id.nullable
    assert {"provider_account_revision", "model_deployment_revision", "execution_class"}.issubset(
        invocation.c.keys()
    )


def test_code_finding_requires_exact_source_anchor_when_causal() -> None:
    sql = _check_sql("investigation_code_findings")

    assert "source_artifact_id IS NOT NULL" in sql
    assert "source_assessment_id IS NOT NULL" in sql
    assert "repository_id IS NOT NULL" in sql
    assert "start_line IS NOT NULL" in sql
    assert "end_line IS NOT NULL" in sql


def test_report_keeps_incident_cause_and_code_diagnosis_separate() -> None:
    columns = set(Base.metadata.tables["investigation_reports"].c.keys())
    assert {
        "incident_cause",
        "code_diagnosis",
        "participants",
        "timeline_summary",
        "source_assessments",
        "configuration_assessments",
        "counter_evidence",
        "evidence_gaps",
    }.issubset(columns)


def test_investigation_schema_contains_no_removed_identity_columns() -> None:
    removed = {"service" + "_id", "service" + "_name", "environment", "request" + "_id"}
    for table_name in (
        "investigations",
        "investigation_inputs",
        "source_revisions",
        "source_assessments",
    ):
        assert removed.isdisjoint(Base.metadata.tables[table_name].c.keys())
