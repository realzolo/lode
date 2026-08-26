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
        reasons: list[str] = []
        if code_status == "confirmed":
            if not any(item.permits_confirmed_code for item in source_assessments):
                reasons.append("confirmed_source_authority_missing")
            if any(item.status == "contradicted" for item in source_assessments):
                reasons.append("runtime_source_contradicted")
        incident = normalized.get("incident_cause", {})
        if (
            isinstance(incident, dict)
            and incident.get("status") == "confirmed"
            and incident.get("mechanism") == "configuration"
            and (
                not configuration_assessments
                or any(item.status != "corroborated" for item in configuration_assessments)
            )
        ):
            reasons.append("runtime_configuration_not_corroborated")
        if (
            result_state == "confirmed" or code_status == "confirmed"
        ) and verifier_status != "approved":
            reasons.append("independent_verifier_not_approved")
        if reasons:
            result_state = "hypothesis"
            if code_status == "confirmed":
                code_status = "hypothesis"
                if isinstance(code, dict):
                    code["status"] = "hypothesis"
            normalized["result_state"] = "hypothesis"
        return ConclusionValidationResult(
            result_state=result_state,
            code_status=code_status,
            reasons=tuple(reasons),
            report=normalized,
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(child) for child in value]
    return value
