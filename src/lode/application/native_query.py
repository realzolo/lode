"""Strict model boundary and server-owned assembly for native query generation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import Field, model_validator

from lode.domain.investigation import PlannedOperation
from lode.evidence_access.candidate import (
    MAX_CANDIDATE_BYTES,
    NativeReadCandidateInput,
    parse_candidate_json,
)
from lode.structured_output import StrictResponseModel, parse_json_document


class NativeQueryPayload(StrictResponseModel):
    payload_json: str = Field(min_length=2, max_length=MAX_CANDIDATE_BYTES)

    @model_validator(mode="after")
    def payload_is_bounded_object(self):
        payload = parse_json_document(self.payload_json)
        if not isinstance(payload, Mapping):
            raise ValueError("payload_json must encode one JSON object")  # noqa: TRY004
        return self

    @property
    def provider_payload(self) -> Mapping[str, Any]:
        value = parse_json_document(self.payload_json)
        assert isinstance(value, Mapping)
        return value


def canonical_value_ref_sentinel(value_ref: str) -> str:
    if not value_ref or value_ref != value_ref.strip() or len(value_ref) > 200:
        raise ValueError("ValueRef is invalid")
    label = re.sub(r"[^A-Z0-9]+", "_", value_ref.upper()).strip("_")[:80] or "VALUE"
    digest = hashlib.sha256(value_ref.encode("utf-8")).hexdigest()[:12].upper()
    return f"__LODE_VALUE_REF_{label}_{digest}__"


def assemble_native_candidate(
    *,
    operation: PlannedOperation,
    connector_id: int,
    language: str,
    available_value_refs: frozenset[str],
    requested_window: Mapping[str, str],
    requested_limit: int,
    requested_timeout_ms: int,
    payload: NativeQueryPayload,
) -> NativeReadCandidateInput:
    sentinel_by_ref = {
        value_ref: canonical_value_ref_sentinel(value_ref)
        for value_ref in sorted(available_value_refs)
    }
    ref_by_sentinel = {sentinel: value_ref for value_ref, sentinel in sentinel_by_ref.items()}
    rendered_payload = json.dumps(
        payload.provider_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    observed_sentinels = {
        sentinel for sentinel in ref_by_sentinel if sentinel in rendered_payload
    }
    residual = rendered_payload
    for sentinel in observed_sentinels:
        residual = residual.replace(sentinel, "")
    if "__LODE_VALUE_REF_" in residual:
        raise ValueError("payload contains an unknown or malformed ValueRef sentinel")
    document = {
        "schema_version": "native-read-candidate.v1",
        "action_id": operation.action_id,
        "connector_id": connector_id,
        "language": language,
        "purpose": operation.purpose,
        "expected_evidence": operation.expected_evidence,
        "evidence_anchors": list(operation.evidence_anchors),
        "payload": dict(payload.provider_payload),
        "value_bindings": {
            sentinel: ref_by_sentinel[sentinel] for sentinel in sorted(observed_sentinels)
        },
        "requested_window": dict(requested_window),
        "requested_limit": requested_limit,
        "requested_timeout_ms": requested_timeout_ms,
    }
    return parse_candidate_json(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def native_query_json_schema() -> dict[str, Any]:
    return NativeQueryPayload.response_json_schema()
