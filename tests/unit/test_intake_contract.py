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
        "schema_version": "incident.alert.v2",
        "source_event_id": "alert-1",
        "dedup_key": "payment.order-create",
        "event_kind": "firing",
        "occurred_at": "2026-08-26T09:30:00.000Z",
        "severity": "CRITICAL",
        "event": "payment.order_create.failed",
        "component": "payment-api",
        "environment": "production",
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


@pytest.mark.parametrize("revision", ["a" * 39, "A" * 40, "main"])
def test_kafka_source_revision_is_optional_but_must_be_a_full_lowercase_sha(revision: str) -> None:
    payload = _payload()
    payload["source_revision"] = revision
    with pytest.raises(ValidationError):
        KafkaIncidentAlert.model_validate(payload)


def test_kafka_source_revision_can_be_omitted() -> None:
    payload = _payload()
    del payload["source_revision"]
    assert KafkaIncidentAlert.model_validate(payload).source_revision is None


def test_kafka_timestamp_requires_timezone() -> None:
    payload = _payload()
    payload["occurred_at"] = "2026-08-26T09:30:00"
    with pytest.raises(ValidationError, match="timezone"):
        KafkaIncidentAlert.model_validate(payload)


def test_manual_and_kafka_use_the_same_normalized_error_shape() -> None:
    kafka = normalize_kafka(KafkaIncidentAlert.model_validate(_payload("trace")))
    manual = normalize_manual(
        ManualIncidentRequest.model_validate(
            {
                "workspace_id": 7,
                "dedup_key": "payment.order-create",
                "occurred_at": "2026-08-26T09:30:00+00:00",
                "severity": "CRITICAL",
                "event": "payment.order_create.failed",
                "component": "payment-api",
                "environment": "production",
                "trace_id": "trace",
                "source_revision": "a" * 40,
                "error": _payload()["error"],
            }
        )
    )

    assert kafka.error_masked == manual.error_masked
    assert kafka.occurred_at == manual.occurred_at
    assert kafka.occurred_at.tzinfo == UTC
    assert manual.source_event_id is None


def test_manual_request_rejects_removed_scope_fields() -> None:
    payload = {
        "workspace_id": 1,
        "dedup_key": "manual.failure",
        "occurred_at": "2026-08-26T09:30:00Z",
        "event": "manual.failure",
        "component": "manual",
        "environment": "production",
        "error": _payload()["error"],
        _REMOVED_SERVICE_FIELD: "api",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ManualIncidentRequest.model_validate(payload)


def test_pre_validation_failure_payload_hides_trace_value() -> None:
    masked, _ = mask_failure_payload(
        {"trace_id": "opaque secret-like value", _REMOVED_SERVICE_FIELD: "removed"}
    )

    assert masked["trace_id"] == "<VALUE_REF:incident.trace_id>"
    assert "opaque secret-like value" not in str(masked)
