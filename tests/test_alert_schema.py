"""Validation of the locked Kafka alert message format (spec alert.v1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.consumer.alert_schema import AlertMessage


def _valid() -> dict:
    return {
        "schema_version": "alert.v1",
        "alert_id": "PB_3q2rj8sKd8",
        "occurred_at": "2026-08-21T15:00:00+08:00",
        "event_type": "checkout_error",
        "level": "CRITICAL",
        "title": "Checkout failed: balance validation timeout",
        "dedupe_key": "alert:checkout_error:9f2c1a7b3e",
        "dedupe_ttl_seconds": 300,
        "fields": {"error": "TimeoutException", "orderId": "20260821000123"},
        "error_log": {
            "name": "TimeoutException",
            "message": "balance validation timed out after 8000ms",
            "stack": None,
            "properties": {"orderId": "20260821000123"},
            "cause": None,
        },
    }


def test_valid_message_passes():
    msg = AlertMessage(**_valid())
    assert msg.level_value == "CRITICAL"
    assert msg.alert_id == "PB_3q2rj8sKd8"
    assert msg.fields["orderId"] == "20260821000123"
    assert msg.error_log is not None
    assert msg.error_log.message == "balance validation timed out after 8000ms"


def test_missing_required_field_fails():
    payload = _valid()
    del payload["title"]
    with pytest.raises(ValidationError):
        AlertMessage(**payload)


def test_wrong_schema_version_fails():
    payload = _valid()
    for bad in ("1.1", "alert.v2", "1.0", "2.0", "1", "alert.v1.0"):
        bad_payload = {**payload, "schema_version": bad}
        with pytest.raises(ValidationError):
            AlertMessage(**bad_payload)


def test_invalid_level_fails():
    payload = _valid()
    payload["level"] = "INFO"
    with pytest.raises(ValidationError):
        AlertMessage(**payload)


def test_optional_error_log_is_optional():
    payload = _valid()
    del payload["error_log"]
    msg = AlertMessage(**payload)
    assert msg.error_log is None


def test_occurred_at_parses_iso8601_with_tz():
    msg = AlertMessage(**_valid())
    assert msg.occurred_at.year == 2026
    assert msg.occurred_at.utcoffset().total_seconds() == 8 * 3600
