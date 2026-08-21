"""Validation of the locked Kafka alert message format (spec v1.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from incident_trace.consumer.alert_schema import AlertMessage


def _valid() -> dict:
    return {
        "schema_version": "1.1",
        "level": "CRITICAL",
        "title": "Checkout failed: balance validation timeout",
        "env": "production",
        "timestamp": "2026-08-21T15:00:00+08:00",
        "event_type": "checkout_error",
        "project": "PornBox App",
        "fields": {"error": "TimeoutException", "orderId": "20260821000123"},
    }


def test_valid_message_passes():
    msg = AlertMessage(**_valid())
    assert msg.level_value == "CRITICAL"
    assert msg.fields["orderId"] == "20260821000123"


def test_missing_required_field_fails():
    payload = _valid()
    del payload["timestamp"]
    with pytest.raises(ValidationError):
        AlertMessage(**payload)


def test_wrong_schema_version_fails():
    payload = _valid()
    payload["schema_version"] = "1.0"
    with pytest.raises(ValidationError):
        AlertMessage(**payload)


def test_invalid_level_fails():
    payload = _valid()
    payload["level"] = "INFO"
    with pytest.raises(ValidationError):
        AlertMessage(**payload)


def test_optional_fields_are_optional():
    payload = _valid()
    del payload["event_type"]
    del payload["project"]
    del payload["fields"]
    msg = AlertMessage(**payload)
    assert msg.event_type is None
    assert msg.fields == {}


def test_timestamp_parses_iso8601_with_tz():
    msg = AlertMessage(**_valid())
    assert msg.timestamp.year == 2026
    assert msg.timestamp.utcoffset().total_seconds() == 8 * 3600
