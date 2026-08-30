from __future__ import annotations

import random

from lode.application.intake import KafkaIncidentAlert, normalize_kafka


def _payload(trace_id: str) -> dict:
    return {
        "schema_version": "incident.alert.v1",
        "alert_id": "fuzz-alert",
        "occurred_at": "2026-08-27T12:00:00+08:00",
        "severity": "WARNING",
        "event": "checkout.failed",
        "trace_id": trace_id,
        "source_revision": "a" * 40,
        "error": {
            "type": "RuntimeError",
            "message": "request failed",
            "stack": "frame:1",
            "cause": None,
        },
    }


def test_opaque_trace_fuzz_is_exact_and_never_persisted_in_masked_payload() -> None:
    generator = random.Random(0x10DE)
    alphabet = (
        "abcXYZ019 -_./:?&=%'\"`$()[]{}\\\n\t\u4e2d\u6587\u03bb\u0416\U0001f680\u200d\u202e\x00"
    )
    traces = [
        "",
        " ' OR 1=1 --",
        "../../etc/passwd",
        '{app="api"} |= `panic`',
    ]
    traces.extend(
        "".join(generator.choice(alphabet) for _ in range(generator.randrange(0, 513)))
        for _ in range(256)
    )

    for trace_id in traces:
        message = KafkaIncidentAlert.model_validate(_payload(trace_id))
        normalized = normalize_kafka(message)

        assert message.trace_id == trace_id
        assert normalized.trace_id == trace_id
        assert normalized.raw_payload_masked["trace_id"] == "<VALUE_REF:incident.trace_id>"
        if trace_id:
            assert trace_id not in str(normalized.raw_payload_masked)
