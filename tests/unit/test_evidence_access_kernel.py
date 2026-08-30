from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from lode.evidence_access.authorizer import _call_policy
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import NativeReadCandidateInput, parse_candidate_json
from lode.evidence_access.mock import MockTreePolicy
from lode.evidence_access.registry import NativePolicyRegistry
from lode.evidence_access.tokens import AuthorizationTokenError, issue_token, verify_token
from lode.evidence_access.types import AccessContext, AccessRejection

SENTINEL = "__LODE_VALUE_REF_INCIDENT_TRACE__"


def candidate_dict() -> dict:
    return {
        "schema_version": "native-read-candidate.v1",
        "action_id": "evidence.followup.1",
        "connector_id": 7,
        "language": "elasticsearch_query_dsl",
        "purpose": "Find matching trace evidence",
        "expected_evidence": "One matching record",
        "evidence_anchors": ["incident.trace_id"],
        "payload": {
            "path": "/logs/_search",
            "body": {"query": {"term": {"trace.id": SENTINEL}}},
        },
        "value_bindings": {SENTINEL: "incident.trace_id"},
        "requested_window": {
            "start": "2026-08-26T09:15:00Z",
            "end": "2026-08-26T09:45:00Z",
        },
        "requested_limit": 2_000,
        "requested_timeout_ms": 60_000,
    }


def context(**changes) -> AccessContext:
    values = {
        "investigation_id": 1,
        "operation_id": 2,
        "connector_snapshot_id": 3,
        "model_invocation_id": 4,
        "workspace_id": 5,
        "connector_id": 7,
        "snapshot_hash": "a" * 64,
        "allowed_languages": ("elasticsearch_query_dsl",),
        "allowed_evidence_anchors": ("incident.trace_id", "assertion:h1"),
        "scope_config": {"allowed_paths": ["/logs/_search"]},
        "schema_catalog": {"fields": ["trace.id"]},
        "execution_budget_policy": {
            "max_result_limit": 500,
            "max_timeout_ms": 10_000,
            "max_output_bytes": 1_000_000,
            "max_total_output_bytes": 2_000_000,
            "max_window_seconds": 900,
            "max_native_reads": 8,
            "max_parallel_operations": 1,
            "estimated_cost": 0.0,
        },
        "investigation_window_start": datetime(2026, 8, 26, 9, 10, tzinfo=UTC),
        "investigation_window_end": datetime(2026, 8, 26, 9, 50, tzinfo=UTC),
    }
    values.update(changes)
    return AccessContext(**values)


def candidate() -> NativeReadCandidateInput:
    return NativeReadCandidateInput.model_validate(candidate_dict())


def test_strict_candidate_rejects_duplicate_and_unknown_json_fields() -> None:
    raw = json.dumps(candidate_dict())
    duplicated = raw.replace('"action_id":', '"action_id":"first","action_id":', 1)
    unknown = candidate_dict()
    unknown["legacy_query"] = "ignored"

    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_candidate_json(duplicated)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        NativeReadCandidateInput.model_validate(unknown)


def test_payload_must_match_language_and_json_body_is_not_a_string() -> None:
    wrong_language = candidate_dict()
    wrong_language["language"] = "command"
    string_body = candidate_dict()
    string_body["payload"]["body"] = '{"query":{"match_all":{}}}'

    with pytest.raises(ValidationError, match="payload"):
        NativeReadCandidateInput.model_validate(wrong_language)
    with pytest.raises(ValidationError, match="dictionary"):
        NativeReadCandidateInput.model_validate(string_body)


def test_candidate_rejects_excessive_depth_and_invalid_unicode() -> None:
    deep: dict = {}
    current = deep
    for _ in range(70):
        child: dict = {}
        current["next"] = child
        current = child
    payload = candidate_dict()
    payload["payload"]["body"] = deep
    invalid = candidate_dict()
    invalid["purpose"] = "bad\ud800"

    with pytest.raises(ValidationError, match="depth limit"):
        NativeReadCandidateInput.model_validate(payload)
    with pytest.raises(ValidationError, match="unicode string"):
        NativeReadCandidateInput.model_validate(invalid)


def test_mock_policy_binds_arbitrary_value_only_at_exact_value_node() -> None:
    policy = MockTreePolicy()
    parsed = policy.parse(candidate())
    evaluation = policy.evaluate(parsed, candidate(), context())
    raw = '"},"script":{"source":"delete everything"}'

    bound = policy.bind_values(parsed, evaluation, {SENTINEL: raw})

    assert bound.structural_hash == parsed.structural_hash
    assert bound.canonical_action["body"]["query"]["term"]["trace.id"] == raw
    assert "script" not in bound.canonical_action["body"]


def test_sentinel_must_be_complete_value_node() -> None:
    payload = candidate_dict()
    payload["payload"]["body"]["query"]["term"]["trace.id"] = f"prefix-{SENTINEL}"

    with pytest.raises(AccessRejection, match="complete JSON value node") as rejected:
        MockTreePolicy().parse(NativeReadCandidateInput.model_validate(payload))
    assert rejected.value.code == "invalid_syntax"


def test_budget_is_intersected_and_exhaustion_rejects() -> None:
    effective, diff = intersect_budget(candidate(), context())

    assert effective.result_limit == 500
    assert effective.timeout_ms == 10_000
    assert effective.window_end - effective.window_start == timedelta(minutes=15)
    assert set(diff) == {"requested_limit", "requested_timeout_ms", "requested_window"}

    with pytest.raises(AccessRejection, match="operation budget exhausted") as exhausted:
        intersect_budget(candidate(), context(native_reads_used=8))
    assert exhausted.value.code == "budget_violation"


def test_registry_never_falls_back_to_partial_parser() -> None:
    registry = NativePolicyRegistry()

    with pytest.raises(AccessRejection, match="no complete parser") as rejected:
        registry.require("logql")
    assert rejected.value.code == "unsupported_node"
    registry.register(MockTreePolicy())
    assert registry.require("elasticsearch_query_dsl").parser_name == "mock-json-tree"
    with pytest.raises(ValueError, match="already registered"):
        registry.register(MockTreePolicy())


@pytest.mark.parametrize(
    "error",
    [ValueError("raw secret"), TypeError("raw secret"), KeyError("raw secret")],
)
def test_policy_processing_errors_become_non_sensitive_stable_rejections(error: Exception) -> None:
    def fail() -> None:
        raise error

    with pytest.raises(AccessRejection) as rejected:
        _call_policy("bind_values", fail)

    assert rejected.value.code == "invalid_syntax"
    assert rejected.value.detail == {
        "stage": "bind_values",
        "error_type": type(error).__name__,
    }
    assert "raw secret" not in rejected.value.reason


def test_authorization_token_is_signed_bound_and_expiring() -> None:
    expires = datetime.now(UTC) + timedelta(seconds=30)
    claims = {
        "investigation_id": 1,
        "candidate_hash": "a" * 64,
        "decision_hash": "b" * 64,
        "snapshot_hash": "c" * 64,
        "policy_hash": "d" * 64,
        "effective_action_hash": "e" * 64,
        "expires_at": expires.isoformat(),
    }
    token = issue_token(claims, key="authorization-key")

    assert verify_token(token, key="authorization-key")["decision_hash"] == "b" * 64
    with pytest.raises(AuthorizationTokenError, match="signature"):
        verify_token(token + "x", key="authorization-key")
    with pytest.raises(AuthorizationTokenError, match="expired"):
        verify_token(token, key="authorization-key", now=expires)
    with pytest.raises(AuthorizationTokenError, match="required"):
        issue_token(claims, key="")
