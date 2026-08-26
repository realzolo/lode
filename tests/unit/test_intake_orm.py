"""Intake ORM tests."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from lode.db import models  # noqa: F401
from lode.db.base import Base


def test_kafka_position_and_producer_identity_have_separate_unique_keys() -> None:
    ingestion = Base.metadata.tables["ingestion_events"]
    alerts = Base.metadata.tables["alerts"]

    ingestion_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in ingestion.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    alert_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in alerts.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("topic", "partition", "offset") in ingestion_unique
    assert ("workspace_id", "alert_id") in alert_unique


def test_alert_stores_final_contract_without_removed_scope_fields() -> None:
    columns = set(Base.metadata.tables["alerts"].c.keys())

    assert {
        "workspace_id",
        "alert_id",
        "occurred_at",
        "severity",
        "event",
        "trace_id_ciphertext",
        "trace_id_hash",
        "source_revision",
        "error",
        "raw_payload_masked",
    }.issubset(columns)
    removed = {"service" + "_name", "environment", "request" + "_id", "git" + "_commit"}
    assert removed.isdisjoint(columns)


def test_trace_value_is_not_stored_in_plaintext() -> None:
    columns = set(Base.metadata.tables["alerts"].c.keys())
    assert "trace_id" not in columns
    assert {"trace_id_ciphertext", "trace_id_hash"}.issubset(columns)


def test_incident_active_signature_is_partial_unique() -> None:
    incident = Base.metadata.tables["incidents"]
    index = next(index for index in incident.indexes if index.name == "uq_incident_active_signature")

    assert index.unique
    assert index.dialect_options["postgresql"]["where"] is not None


def test_job_schema_supports_skip_locked_lease_recovery() -> None:
    job = Base.metadata.tables["investigation_jobs"]
    columns = set(job.c.keys())

    assert {"status", "available_at", "claimed_by", "lease_expires_at", "attempt_count"}.issubset(columns)
    assert any(index.name == "ix_investigation_jobs_claim" for index in job.indexes)


def test_intake_enums_and_hashes_are_database_constrained() -> None:
    alerts = Base.metadata.tables["alerts"]
    incidents = Base.metadata.tables["incidents"]
    alert_sql = " ".join(
        str(constraint.sqltext)
        for constraint in alerts.constraints
        if isinstance(constraint, CheckConstraint)
    )
    incident_sql = " ".join(
        str(constraint.sqltext)
        for constraint in incidents.constraints
        if isinstance(constraint, CheckConstraint)
    )

    assert "CRITICAL" in alert_sql and "WARNING" in alert_sql
    assert "40" in alert_sql
    assert "signature_hash" in incident_sql
