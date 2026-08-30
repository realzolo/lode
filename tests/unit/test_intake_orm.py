"""Intake ORM tests."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from lode.db import models  # noqa: F401
from lode.db.base import Base


def test_kafka_position_and_source_event_identity_have_separate_unique_keys() -> None:
    ingestion = Base.metadata.tables["ingestion_events"]
    occurrences = Base.metadata.tables["incident_occurrences"]

    ingestion_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in ingestion.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("topic", "partition", "offset") in ingestion_unique
    source_index = next(
        index
        for index in occurrences.indexes
        if index.name == "uq_incident_occurrence_source_event"
    )
    assert source_index.unique
    assert source_index.dialect_options["postgresql"]["where"] is not None


def test_occurrence_stores_the_final_incident_contract_without_plaintext_trace() -> None:
    columns = set(Base.metadata.tables["incident_occurrences"].c.keys())

    assert {
        "workspace_id",
        "incident_id",
        "source_event_id",
        "dedup_key",
        "event_kind",
        "occurred_at",
        "severity",
        "event",
        "component",
        "environment",
        "trace_id_ciphertext",
        "trace_id_hash",
        "source_revision",
        "error",
        "raw_payload_masked",
    }.issubset(columns)
    assert "trace_id" not in columns


def test_active_incident_dedup_key_is_partial_unique() -> None:
    incident = Base.metadata.tables["incidents"]
    index = next(
        index for index in incident.indexes if index.name == "uq_incident_active_dedup_key"
    )

    assert index.unique
    assert index.dialect_options["postgresql"]["where"] is not None


def test_incident_model_has_operational_lifecycle_and_append_only_history() -> None:
    incident = Base.metadata.tables["incidents"]
    occurrence = Base.metadata.tables["incident_occurrences"]
    event = Base.metadata.tables["incident_events"]

    assert {
        "state",
        "state_changed_at",
        "state_version",
        "recurrence_of_id",
        "assigned_to",
    }.issubset(incident.c.keys())
    assert {"incident_id", "event_type", "actor_id", "payload"}.issubset(event.c.keys())
    occurrence_sql = " ".join(
        str(constraint.sqltext)
        for constraint in occurrence.constraints
        if isinstance(constraint, CheckConstraint)
    )
    incident_sql = " ".join(
        str(constraint.sqltext)
        for constraint in incident.constraints
        if isinstance(constraint, CheckConstraint)
    )
    assert "firing" in occurrence_sql and "recovered" in occurrence_sql
    assert "acknowledged" in incident_sql and "closed" in incident_sql


def test_investigation_is_an_incident_owned_run() -> None:
    investigation = Base.metadata.tables["investigations"]
    columns = set(investigation.c.keys())

    assert {"incident_id", "trigger_occurrence_id", "trigger_reason", "retry_of_id"}.issubset(
        columns
    )
    assert "alert_id" not in columns
    assert "archived_at" not in columns


def test_job_schema_supports_skip_locked_lease_recovery() -> None:
    job = Base.metadata.tables["investigation_jobs"]
    columns = set(job.c.keys())

    assert {
        "status",
        "phase",
        "available_at",
        "claimed_by",
        "lease_expires_at",
        "attempt_count",
    }.issubset(columns)
    assert any(index.name == "ix_investigation_jobs_claim" for index in job.indexes)
