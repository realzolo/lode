"""Server-owned semantic downgrade gates for incident conclusions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lode.domain.model_execution import (
    ConfigurationAuthorityAssessment,
    SourceAuthorityAssessment,
)


@dataclass(frozen=True, slots=True)
class ConclusionValidationResult:
    result_state: str
    code_status: str
    reasons: tuple[str, ...]
    report: Mapping[str, Any]


class ConclusionValidator:
    def validate(
        self,
        report: Mapping[str, Any],
        *,
        source_assessments: Sequence[SourceAuthorityAssessment],
        configuration_assessments: Sequence[ConfigurationAuthorityAssessment],
        verifier_status: str | None,
    ) -> ConclusionValidationResult:
        normalized = _plain(report)
        result_state = str(normalized.get("result_state", "unavailable"))
        code = normalized.get("code_diagnosis")
        code_status = (
            str(code.get("status", "not_found")) if isinstance(code, dict) else "not_found"
        )
        code_reasons: list[str] = []
        if code_status == "confirmed":
            if not any(item.permits_confirmed_code for item in source_assessments):
                code_reasons.append("confirmed_source_authority_missing")
            if any(
                item.authority_status == "contradicted"
                or item.compatibility_status == "incompatible"
                for item in source_assessments
            ):
                code_reasons.append("source_snapshot_incompatible")
        if code_reasons:
            code_status = "hypothesis"
            if isinstance(code, dict):
                code["status"] = "hypothesis"
        incident = normalized.get("incident_cause", {})
        incident_status = (
            str(incident.get("status", "not_found")) if isinstance(incident, dict) else "not_found"
        )
        conclusion_reasons: list[str] = []
        if result_state == "confirmed":
            if incident_status != "confirmed" and code_status != "confirmed":
                conclusion_reasons.append("confirmed_conclusion_anchor_missing")
            if incident_status == "confirmed" and not incident.get("evidence_refs"):
                conclusion_reasons.append("confirmed_incident_evidence_missing")
            if code_status == "confirmed" and (
                not isinstance(code, dict) or not code.get("finding_refs")
            ):
                conclusion_reasons.append("confirmed_code_finding_missing")
        if (
            isinstance(incident, dict)
            and incident_status == "confirmed"
            and incident.get("mechanism") == "configuration"
            and (
                not configuration_assessments
                or any(item.status != "corroborated" for item in configuration_assessments)
            )
        ):
            conclusion_reasons.append("runtime_configuration_not_corroborated")
        if (
            result_state == "confirmed" or code_status == "confirmed"
        ) and verifier_status != "approved":
            conclusion_reasons.append("independent_verifier_not_approved")
        if conclusion_reasons:
            result_state = "hypothesis"
            if code_status == "confirmed":
                code_status = "hypothesis"
                if isinstance(code, dict):
                    code["status"] = "hypothesis"
            normalized["result_state"] = "hypothesis"
        return ConclusionValidationResult(
            result_state=result_state,
            code_status=code_status,
            reasons=(*code_reasons, *conclusion_reasons),
            report=normalized,
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(child) for child in value]
    return value
