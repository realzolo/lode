"""Deterministic validation for model-generated context summaries."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from lode.domain.model_execution import ContextEvidence
from lode.structured_output import StrictResponseModel, parse_json_document

_STABLE_SCALAR = re.compile(
    r"(?:\b\d+(?:\.\d+)?\b|\b[0-9a-f]{40}\b|\b[0-9a-f]{64}\b|"
    r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\b\d{4}-\d{2}-\d{2}T[^\s\"']+)",
    re.IGNORECASE,
)


class ContextSummaryPayload(StrictResponseModel):
    summary_json: str = Field(max_length=256 * 1024)
    input_evidence_refs: tuple[int, ...] = Field(min_length=1)
    covered_claim_refs: tuple[int, ...]
    retained_counter_evidence_refs: tuple[int, ...]
    omitted_evidence_refs: tuple[int, ...]

    @field_validator("summary_json")
    @classmethod
    def summary_is_a_json_object(cls, value: str) -> str:
        decoded = parse_json_document(value)
        if not isinstance(decoded, dict):
            raise ValueError("context summary must be a JSON object")
        return value

    @property
    def summary(self) -> Mapping[str, Any]:
        decoded = parse_json_document(self.summary_json)
        if not isinstance(decoded, dict):
            raise TypeError("context summary must be a JSON object")
        return decoded


@dataclass(frozen=True, slots=True)
class ContextSummaryValidation:
    valid: bool
    codes: tuple[str, ...]


class ContextSummaryValidator:
    def validate(
        self,
        payload: ContextSummaryPayload,
        evidence: Sequence[ContextEvidence],
    ) -> ContextSummaryValidation:
        by_id = {item.artifact_id: item for item in evidence}
        input_refs = tuple(payload.input_evidence_refs)
        codes: list[str] = []
        if len(input_refs) != len(set(input_refs)) or set(input_refs) != set(by_id):
            codes.append("summary_input_refs_mismatch")
        if any(item.pinned for item in evidence):
            codes.append("pinned_evidence_must_not_be_summarized")
        counters = {item.artifact_id for item in evidence if item.counter_evidence}
        retained = set(payload.retained_counter_evidence_refs)
        if not counters.issubset(retained):
            codes.append("counter_evidence_not_retained")
        if not retained.issubset(by_id):
            codes.append("summary_counter_ref_unknown")
        omitted = set(payload.omitted_evidence_refs)
        if not omitted.issubset(by_id) or omitted & retained:
            codes.append("summary_omission_refs_invalid")
        if not set(payload.covered_claim_refs).issubset(by_id):
            codes.append("summary_claim_ref_unknown")
        source_text = json.dumps(
            [_plain(item.content) for item in evidence],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        summary_text = json.dumps(
            payload.summary,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        source_scalars = set(_STABLE_SCALAR.findall(source_text))
        if not set(_STABLE_SCALAR.findall(summary_text)).issubset(source_scalars):
            codes.append("summary_stable_scalar_drift")
        return ContextSummaryValidation(not codes, tuple(codes))


def context_summary_json_schema() -> dict[str, Any]:
    return ContextSummaryPayload.response_json_schema()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(child) for child in value]
    return value
