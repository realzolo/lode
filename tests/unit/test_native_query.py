from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lode.application.native_query import (
    NativeQueryPayload,
    assemble_native_candidate,
    canonical_value_ref_sentinel,
)
from lode.domain.investigation import PlannedOperation


def operation() -> PlannedOperation:
    return PlannedOperation(
        action_id="native:17:logql",
        purpose="Find the incident trace in the bounded log window",
        expected_evidence="A trace-bound payment failure event",
        evidence_anchors=("incident.trace_id",),
        supports_hypotheses=("h1",),
        refutes_hypotheses=(),
        selection_reason="Runtime logs can close the current evidence gap",
        stop_condition="Stop after the bounded result limit",
        estimated_cost=0.0,
    )


def payload(*, query: str) -> NativeQueryPayload:
    return NativeQueryPayload(
        payload_json=json.dumps({"query": query}),
    )


def test_server_assembles_native_candidate_from_operation_owned_fields() -> None:
    sentinel = canonical_value_ref_sentinel("incident.trace_id")

    candidate = assemble_native_candidate(
        operation=operation(),
        connector_id=23,
        language="logql",
        available_value_refs=frozenset({"incident.trace_id"}),
        requested_window={"start": "2026-08-29T00:00:00Z", "end": "2026-08-29T00:05:00Z"},
        requested_limit=100,
        requested_timeout_ms=5_000,
        payload=payload(
            query=f'{{service="payments"}} | json | trace_id = "{sentinel}"',
        ),
    )

    assert candidate.action_id == "native:17:logql"
    assert candidate.connector_id == 23
    assert candidate.purpose == operation().purpose
    assert candidate.value_bindings == {sentinel: "incident.trace_id"}
    assert candidate.payload.query.endswith(f'"{sentinel}"')
    assert candidate.requested_limit == 100
    assert candidate.requested_timeout_ms == 5_000


def test_value_ref_sentinel_is_canonical_and_collision_resistant() -> None:
    first = canonical_value_ref_sentinel("incident.trace_id")
    second = canonical_value_ref_sentinel("incident-trace-id")

    assert first.startswith("__LODE_VALUE_REF_INCIDENT_TRACE_ID_")
    assert first.endswith("__")
    assert first != second


def test_model_cannot_invent_or_transform_value_ref_sentinels() -> None:
    with pytest.raises(ValueError, match="unknown or malformed"):
        assemble_native_candidate(
            operation=operation(),
            connector_id=23,
            language="logql",
            available_value_refs=frozenset({"incident.trace_id"}),
            requested_window={
                "start": "2026-08-29T00:00:00Z",
                "end": "2026-08-29T00:05:00Z",
            },
            requested_limit=100,
            requested_timeout_ms=5_000,
            payload=payload(
                query='{service="payments"} | trace_id = "__LODE_VALUE_REF_incident.trace_id__"',
            ),
        )


def test_model_cannot_use_a_canonical_but_unavailable_value_ref() -> None:
    with pytest.raises(ValueError, match="unknown or malformed"):
        assemble_native_candidate(
            operation=operation(),
            connector_id=23,
            language="logql",
            available_value_refs=frozenset({"incident.trace_id"}),
            requested_window={
                "start": "2026-08-29T00:00:00Z",
                "end": "2026-08-29T00:05:00Z",
            },
            requested_limit=100,
            requested_timeout_ms=5_000,
            payload=payload(query=canonical_value_ref_sentinel("secret.api_key")),
        )


def test_native_query_protocol_rejects_legacy_candidate_envelope() -> None:
    with pytest.raises(ValidationError):
        NativeQueryPayload.model_validate(
            {
                "payload_json": '{"query":"{service=\\"payments\\"}"}',
                "connector_id": 23,
            }
        )
