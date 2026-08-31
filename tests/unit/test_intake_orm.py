"""Intake ORM tests."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from lode.db import models  # noqa: F401
from lode.db.base import Base


def test_kafka_position_and_signal_identity_have_separate_unique_keys() -> None:
    ingestion = Base.metadata.tables["ingestion_events"]
    signals = Base.metadata.tables["incident_signals"]

    ingestion_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in ingestion.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("topic", "partition", "offset") in ingestion_unique
    source_index = next(
        index
        for index in signals.indexes
        if index.name == "uq_incident_signal_source_event"
    )
    assert source_index.unique
    assert source_index.dialect_options["postgresql"]["where"] is not None


def test_signal_stores_the_v1_contract_without_plaintext_trace() -> None:
    table = Base.metadata.tables["incident_signals"]
    columns = set(table.c.keys())

    assert {
        "workspace_id",
        "schema_version",
        "source_type",
        "source_event_id",
        "idempotency_key_hash",
        "signal_kind",
        "observed_at",
        "severity",
        "title",
        "summary",
        "repository_binding_id",
        "trace_id_ciphertext",
        "trace_id_hash",
        "source_revision",
        "fingerprint",
        "error_masked",
        "raw_payload_masked",
        "raw_payload_ciphertext",
        "raw_payload_hash",
    }.issubset(columns)
    assert "trace_id" not in columns
    assert {"environment", "component", "dedup_key", "incident_id"}.isdisjoint(columns)


def test_incident_has_no_client_deduplication_or_environment_dimension() -> None:
    incident = Base.metadata.tables["incidents"]
    assert {"dedup_key", "environment", "component"}.isdisjoint(incident.c.keys())
    assert {"title", "severity", "signal_count"}.issubset(incident.c.keys())
    link = Base.metadata.tables["incident_signal_links"]
    assert {"signal_id", "incident_id", "state_version"}.issubset(link.c.keys())


def test_incident_model_has_operational_lifecycle_and_append_only_history() -> None:
    incident = Base.metadata.tables["incidents"]
    signal = Base.metadata.tables["incident_signals"]
    association_event = Base.metadata.tables["incident_signal_association_events"]
    event = Base.metadata.tables["incident_events"]

    assert {
        "state",
        "state_changed_at",
        "state_version",
        "recurrence_of_id",
        "assigned_to",
    }.issubset(incident.c.keys())
    assert {"incident_id", "event_type", "actor_id", "payload"}.issubset(event.c.keys())
    signal_sql = " ".join(
        str(constraint.sqltext)
        for constraint in signal.constraints
        if isinstance(constraint, CheckConstraint)
    )
    incident_sql = " ".join(
        str(constraint.sqltext)
        for constraint in incident.constraints
        if isinstance(constraint, CheckConstraint)
    )
    assert "firing" in signal_sql and "recovered" in signal_sql
    assert "acknowledged" in incident_sql and "closed" in incident_sql
    assert {"signal_id", "incident_id", "event_type", "reason"}.issubset(
        association_event.c.keys()
    )


def test_investigation_is_an_incident_owned_run() -> None:
    investigation = Base.metadata.tables["investigations"]
    columns = set(investigation.c.keys())

    assert {
        "incident_id",
        "trigger_signal_id",
        "trigger_reason",
        "parent_investigation_id",
        "window_expansion_level",
    }.issubset(columns)
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
