"""Validation of the strict incident alert contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.consumer.alert_schema import AlertMessage, normalize_alert_error


def _valid() -> dict:
    return {
        "schema_version": "incident.alert.v1",
        "alert_id": "PB_3q2rj8sKd8",
        "occurred_at": "2026-08-21T15:00:00+08:00",
        "severity": "CRITICAL",
        "event": "payment.order_create.failed",
        "service_name": "pornbox",
        "environment": "prod",
        "git_commit": "6c36658895cb220b66f89f17718a001f3f9f02e4",
        "request_id": "4bf92f35-77b3-4daa-b3ce-929d0e0e4736",
        "correlation": {"order_id": "20260821000123"},
        "error": {
            "type": "TimeoutException",
            "message": "balance validation timed out after 8000ms",
            "stack": "TimeoutException: balance validation timed out",
            "cause": None,
        },
    }


def test_valid_message_passes() -> None:
    message = AlertMessage.model_validate(_valid())
    normalized = normalize_alert_error(message)

    assert message.level_value == "CRITICAL"
    assert str(message.request_id) == "4bf92f35-77b3-4daa-b3ce-929d0e0e4736"
    assert message.correlation.order_id == "20260821000123"
    assert normalized.name == "TimeoutException"
    assert normalized.message == "balance validation timed out after 8000ms"


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "alert_id",
        "occurred_at",
        "severity",
        "event",
        "service_name",
        "environment",
        "git_commit",
        "request_id",
        "error",
    ],
)
def test_required_fields_are_strict(field: str) -> None:
    payload = _valid()
    del payload[field]
    with pytest.raises(ValidationError):
        AlertMessage.model_validate(payload)


@pytest.mark.parametrize(
    "request_id",
    [
        "4bf92f3577b34da6a3ce929d0e0e4736",
        "4bf92f35-77b3-1daa-b3ce-929d0e0e4736",
        "not-a-uuid",
    ],
)
def test_request_id_requires_uuid_v4(request_id: str) -> None:
    with pytest.raises(ValidationError):
        AlertMessage.model_validate({**_valid(), "request_id": request_id})


def test_unknown_fields_and_old_contract_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AlertMessage.model_validate({**_valid(), "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"})
    with pytest.raises(ValidationError):
        AlertMessage.model_validate({**_valid(), "schema_version": "alert.v1"})


def test_invalid_severity_and_naive_timestamp_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AlertMessage.model_validate({**_valid(), "severity": "INFO"})
    with pytest.raises(ValidationError):
        AlertMessage.model_validate({**_valid(), "occurred_at": "2026-08-25T10:38:59"})
