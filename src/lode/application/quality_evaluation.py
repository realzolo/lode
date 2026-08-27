"""Deterministic analysis quality metrics and release-gate evaluation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProportionMetric:
    successes: int
    total: int
    value: float
    wilson_lower_95: float
    wilson_upper_95: float


@dataclass(frozen=True, slots=True)
class IncidentObservation:
    case_id: str
    expected_result_state: str
    actual_result_state: str
    cause_correct: bool
    causal_relation_correct: bool
    evidence_references_complete: bool
    version_gate_satisfied: bool
    counter_evidence_gate_satisfied: bool
    evidence_refs: tuple[int, ...] = ()
    observation_id: str = ""


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    observation_id: str
    case_id: str
    expected: str
    observed: str
    passed: bool
    evidence_refs: tuple[int, ...]
    failure_classification: str | None


@dataclass(frozen=True, slots=True)
class QualityEvaluation:
    metrics: Mapping[str, ProportionMetric]
    cases: tuple[CaseEvaluation, ...]
    smoke_passed: bool
    release_gate_passed: bool
    release_gate_failures: tuple[str, ...]


def wilson_interval(
    successes: int, total: int, *, z: float = 1.959963984540054
) -> tuple[float, float]:
    """Return the two-sided Wilson score interval for a binomial proportion."""

    if total < 1 or successes < 0 or successes > total:
        raise ValueError("successes and total must describe a non-empty binomial sample")
    proportion = successes / total
    z_squared = z * z
    denominator = 1 + z_squared / total
    center = (proportion + z_squared / (2 * total)) / denominator
    spread = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z_squared / (4 * total * total))
        / denominator
    )
    return max(0.0, center - spread), min(1.0, center + spread)


def proportion_metric(successes: int, total: int) -> ProportionMetric:
    lower, upper = wilson_interval(successes, total)
    return ProportionMetric(successes, total, successes / total, lower, upper)


def evaluate_incidents(observations: Sequence[IncidentObservation]) -> QualityEvaluation:
    if not observations:
        raise ValueError("incident observations must not be empty")
    identifiers = [item.observation_id or item.case_id for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("incident observation IDs must be unique")

    confirmed = [item for item in observations if item.actual_result_state == "confirmed"]
    non_confirmed_oracles = [
        item for item in observations if item.expected_result_state != "confirmed"
    ]
    downgrade_oracles = [
        item
        for item in observations
        if item.expected_result_state in {"hypothesis", "insufficient", "unavailable"}
    ]
    if not confirmed or not non_confirmed_oracles or not downgrade_oracles:
        raise ValueError("evaluation corpus must cover confirmed and abstention outcomes")

    metrics = {
        "confirmed_incident_cause_precision": proportion_metric(
            sum(item.cause_correct for item in confirmed), len(confirmed)
        ),
        "false_confirmed_rate": proportion_metric(
            sum(item.actual_result_state == "confirmed" for item in non_confirmed_oracles),
            len(non_confirmed_oracles),
        ),
        "correct_downgrade_rate": proportion_metric(
            sum(
                item.actual_result_state == item.expected_result_state for item in downgrade_oracles
            ),
            len(downgrade_oracles),
        ),
        "confirmed_causal_relation_precision": proportion_metric(
            sum(item.causal_relation_correct for item in confirmed), len(confirmed)
        ),
        "confirmed_evidence_reference_completeness": proportion_metric(
            sum(item.evidence_references_complete for item in confirmed), len(confirmed)
        ),
        "confirmed_version_gate_rate": proportion_metric(
            sum(item.version_gate_satisfied for item in confirmed), len(confirmed)
        ),
        "confirmed_counter_evidence_gate_rate": proportion_metric(
            sum(item.counter_evidence_gate_satisfied for item in confirmed), len(confirmed)
        ),
    }
    cases = tuple(_evaluate_case(item) for item in observations)
    smoke_passed = all(item.passed for item in cases)
    failures = _release_failures(metrics)
    return QualityEvaluation(
        metrics=metrics,
        cases=cases,
        smoke_passed=smoke_passed,
        release_gate_passed=smoke_passed and not failures,
        release_gate_failures=tuple(failures),
    )


def observation_from_oracle(record: Mapping[str, Any]) -> IncidentObservation:
    """Execute the deterministic authority oracle used by the offline smoke suite."""

    case_id = _required_string(record, "case_id")
    expected = _required_string(record, "result_state")
    source_status = _required_string(record, "source_status")
    cause = _required_string(record, "incident_cause")
    verifier_required = bool(record.get("requires_verifier", False))

    if cause == "not_found" and "model-contract-failure" in case_id:
        actual = "unavailable"
    elif cause == "not_found":
        actual = "insufficient"
    elif (
        cause == "application_code"
        and source_status == "exact"
        and verifier_required
        and bool(record.get("has_counter_evidence"))
    ):
        actual = "confirmed"
    else:
        actual = "hypothesis"
    is_confirmed = actual == "confirmed"
    return IncidentObservation(
        case_id=case_id,
        expected_result_state=expected,
        actual_result_state=actual,
        cause_correct=actual == expected,
        causal_relation_correct=actual == expected,
        evidence_references_complete=not is_confirmed or source_status == "exact",
        version_gate_satisfied=not is_confirmed or source_status == "exact",
        counter_evidence_gate_satisfied=(
            not is_confirmed or bool(record.get("has_counter_evidence"))
        ),
    )


def observation_from_result(record: Mapping[str, Any]) -> IncidentObservation:
    refs = record.get("evidence_refs", [])
    if not isinstance(refs, list) or any(not isinstance(item, int) or item < 1 for item in refs):
        raise ValueError("evidence_refs must contain positive integer IDs")
    return IncidentObservation(
        case_id=_required_string(record, "case_id"),
        expected_result_state=_required_string(record, "expected_result_state"),
        actual_result_state=_required_string(record, "actual_result_state"),
        cause_correct=_required_bool(record, "cause_correct"),
        causal_relation_correct=_required_bool(record, "causal_relation_correct"),
        evidence_references_complete=_required_bool(record, "evidence_references_complete"),
        version_gate_satisfied=_required_bool(record, "version_gate_satisfied"),
        counter_evidence_gate_satisfied=_required_bool(record, "counter_evidence_gate_satisfied"),
        evidence_refs=tuple(refs),
        observation_id=_required_string(record, "observation_id"),
    )


def _evaluate_case(observation: IncidentObservation) -> CaseEvaluation:
    passed = observation.actual_result_state == observation.expected_result_state
    return CaseEvaluation(
        observation_id=observation.observation_id or observation.case_id,
        case_id=observation.case_id,
        expected=observation.expected_result_state,
        observed=observation.actual_result_state,
        passed=passed,
        evidence_refs=observation.evidence_refs,
        failure_classification=None if passed else "result_state_mismatch",
    )


def _release_failures(metrics: Mapping[str, ProportionMetric]) -> list[str]:
    failures: list[str] = []
    cause = metrics["confirmed_incident_cause_precision"]
    if cause.value < 0.98:
        failures.append("confirmed_incident_cause_precision_below_98_percent")
    if cause.wilson_lower_95 < 0.95:
        failures.append("confirmed_incident_cause_wilson_lower_below_95_percent")
    if metrics["false_confirmed_rate"].value > 0.01:
        failures.append("false_confirmed_rate_above_1_percent")
    if metrics["correct_downgrade_rate"].value < 0.98:
        failures.append("correct_downgrade_rate_below_98_percent")
    if metrics["confirmed_causal_relation_precision"].value < 0.98:
        failures.append("confirmed_causal_relation_precision_below_98_percent")
    for name in (
        "confirmed_evidence_reference_completeness",
        "confirmed_version_gate_rate",
        "confirmed_counter_evidence_gate_rate",
    ):
        if metrics[name].value != 1.0:
            failures.append(f"{name}_below_100_percent")
    return failures


def _required_string(record: Mapping[str, Any], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _required_bool(record: Mapping[str, Any], name: str) -> bool:
    value = record.get(name)
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value
