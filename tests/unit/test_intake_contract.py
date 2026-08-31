"""Kafka and manual intake contract tests."""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError

from lode.application.intake import (
    KafkaIncidentAlert,
    ManualIncidentRequest,
    mask_failure_payload,
    normalize_kafka,
    normalize_manual,
)

_REMOVED_SERVICE_FIELD = "service" + "_name"
_REMOVED_REQUEST_FIELD = "request" + "_id"
_REMOVED_COMMIT_FIELD = "git" + "_commit"


def _payload(trace_id: str = "opaque") -> dict:
    return {
        "schema_version": "incident.alert.v1",
        "alert_id": "alert-1",
        "occurred_at": "2026-08-26T09:30:00.000Z",
        "severity": "CRITICAL",
        "event": "payment.order_create.failed",
        "trace_id": trace_id,
        "source_revision": "a" * 40,
        "error": {
            "type": "GatewayError",
            "message": "Payment creation failed",
            "stack": "stack",
            "cause": None,
        },
    }


@pytest.mark.parametrize(
    "trace_id",
    [
        "",
        "550e8400-e29b-41d4-a716-446655440000",
        "ordinary-value",
        "  surrounded by spaces  ",
        "引号'\"与中文",
        '{service="api"} |= `error` ; DROP TABLE x --',
        "line one\nline two\tend",
    ],
)
def test_kafka_trace_is_preserved_exactly_and_hidden_from_masked_payload(trace_id: str) -> None:
    message = KafkaIncidentAlert.model_validate(_payload(trace_id))
    normalized = normalize_kafka(message)

    assert message.trace_id == trace_id
    assert normalized.trace_id == trace_id
    assert normalized.raw_payload_masked["trace_id"] == "<VALUE_REF:incident.trace_id>"
    if trace_id:
        assert trace_id not in str(normalized.raw_payload_masked)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (_REMOVED_SERVICE_FIELD, "api"),
        ("component", "api"),
        ("environment", "production"),
        ("source_event_id", "alert-1"),
        ("dedup_key", "payment.order-create"),
        ("event_kind", "firing"),
        (_REMOVED_REQUEST_FIELD, "request-1"),
        (_REMOVED_COMMIT_FIELD, "a" * 40),
        ("correlation", {}),
    ],
)
def test_removed_and_unknown_kafka_fields_are_rejected(field: str, value: object) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        KafkaIncidentAlert.model_validate(payload)


def test_nested_error_unknown_field_is_rejected() -> None:
    payload = _payload()
    payload["error"]["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        KafkaIncidentAlert.model_validate(payload)


@pytest.mark.parametrize("revision", [None, "a" * 39, "A" * 40, "main"])
def test_kafka_source_revision_is_required_full_lowercase_sha(revision: str | None) -> None:
    payload = _payload()
    if revision is None:
        del payload["source_revision"]
    else:
        payload["source_revision"] = revision
    with pytest.raises(ValidationError):
        KafkaIncidentAlert.model_validate(payload)


@pytest.mark.parametrize("field", ["alert_id", "trace_id", "error"])
def test_kafka_v1_required_fields_cannot_be_omitted(field: str) -> None:
    payload = _payload()
    del payload[field]
    with pytest.raises(ValidationError):
        KafkaIncidentAlert.model_validate(payload)


def test_kafka_v1_maps_to_internal_signal_without_invented_context() -> None:
    normalized = normalize_kafka(KafkaIncidentAlert.model_validate(_payload("trace")))

    assert normalized.schema_version == "incident-signal.v1"
    assert normalized.source_type == "kafka"
    assert normalized.source_event_id == "alert-1"
    assert normalized.signal_kind == "firing"
    assert normalized.repository_binding_id is None
    assert normalized.idempotency_key_hash is None
    assert normalized.source_revision == "a" * 40
    assert (
        normalized.fingerprint
        == normalize_kafka(
            KafkaIncidentAlert.model_validate({**_payload("trace"), "alert_id": "alert-2"})
        ).fingerprint
    )
    assert (
        normalized.fingerprint
        == normalize_kafka(KafkaIncidentAlert.model_validate(_payload("other-trace"))).fingerprint
    )


def test_kafka_timestamp_requires_timezone() -> None:
    payload = _payload()
    payload["occurred_at"] = "2026-08-26T09:30:00"
    with pytest.raises(ValidationError, match="timezone"):
        KafkaIncidentAlert.model_validate(payload)


def test_minimal_manual_request_maps_to_unclassified_signal() -> None:
    kafka = normalize_kafka(KafkaIncidentAlert.model_validate(_payload("trace")))
    manual = normalize_manual(
        ManualIncidentRequest.model_validate(
            {
                "schema_version": "manual-incident.v1",
                "summary": "Payment creation failed",
                "error_text": "GatewayError: Payment creation failed\n  at checkout.py:20",
            }
        ),
        idempotency_key="client-generated-request-id",
    )

    assert kafka.schema_version == manual.schema_version == "incident-signal.v1"
    assert manual.source_type == "manual"
    assert manual.severity == "UNCLASSIFIED"
    assert manual.repository_binding_id is None
    assert manual.trace_id is None
    assert manual.source_revision is None
    assert manual.observed_at.tzinfo == UTC
    assert manual.source_event_id is None
    assert len(manual.idempotency_key_hash or "") == 64
    assert manual.raw_payload_masked["schema_version"] == "manual-incident.v1"


def test_manual_optional_trace_and_repository_are_preserved_without_kafka_fields() -> None:
    manual = normalize_manual(
        ManualIncidentRequest.model_validate(
            {
                "schema_version": "manual-incident.v1",
                "summary": "Worker timeout",
                "error_text": "timeout waiting for upstream",
                "trace_id": "trace-7",
                "repository_binding_id": 42,
            }
        ),
        idempotency_key="manual-2",
    )

    assert manual.trace_id == "trace-7"
    assert manual.repository_binding_id == 42
    assert manual.raw_payload_masked["trace_id"] == "<VALUE_REF:incident.trace_id>"


def test_manual_request_rejects_removed_scope_fields() -> None:
    payload = {
        "schema_version": "manual-incident.v1",
        "summary": "Manual failure",
        "error_text": "stack",
        "environment": "production",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ManualIncidentRequest.model_validate(payload)


def test_pre_validation_failure_payload_hides_trace_value() -> None:
    masked, _ = mask_failure_payload(
        {"trace_id": "opaque secret-like value", _REMOVED_SERVICE_FIELD: "removed"}
    )

    assert masked["trace_id"] == "<VALUE_REF:incident.trace_id>"
    assert "opaque secret-like value" not in str(masked)
