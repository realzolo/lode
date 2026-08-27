"""Operational and canary metrics required by the final release gate."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lode.application.quality_evaluation import QualityEvaluation


@dataclass(frozen=True, slots=True)
class OperationalEvaluation:
    metrics: Mapping[str, float]
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanaryEvaluation:
    passed: bool
    failures: tuple[str, ...]


_BASELINE_DIRECTIONS = {
    "connector_useful_call_rate": "minimum",
    "connector_missed_critical_rate": "maximum",
    "connector_zero_call_correct_rate": "minimum",
    "connector_mean_cost": "maximum",
    "connector_mean_latency_ms": "maximum",
    "routing_latency_hit_rate": "minimum",
    "routing_unnecessary_reasoning_rate": "maximum",
    "routing_missed_upgrade_rate": "maximum",
    "routing_compression_correct_rate": "minimum",
    "routing_latency_mean_tokens": "maximum",
    "routing_latency_mean_cost": "maximum",
    "routing_latency_mean_latency_ms": "maximum",
    "routing_reasoning_mean_tokens": "maximum",
    "routing_reasoning_mean_cost": "maximum",
    "routing_reasoning_mean_latency_ms": "maximum",
}


def evaluate_operational(
    cases: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> OperationalEvaluation:
    case_map = {_string(item, "case_id"): item for item in cases}
    if len(case_map) != len(cases):
        raise ValueError("operational case IDs must be unique")
    identifiers = [_string(item, "observation_id") for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("operational observation IDs must be unique")
    observed_cases = {_string(item, "case_id") for item in observations}
    if observed_cases != case_map.keys():
        raise ValueError("operational observations must cover every frozen operational case")

    identity: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    malicious: list[bool] = []
    valid_reads: list[bool] = []
    connector: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    routing: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for observation in observations:
        case = case_map[_string(observation, "case_id")]
        kind = _case_kind(case)
        if kind == "identity":
            identity.append((case, observation))
        elif kind == "security":
            correct = _string(observation, "actual") == _string(case, "expected")
            (valid_reads if case["expected"] == "allow" else malicious).append(correct)
        elif kind == "connector_selection":
            connector.append((case, observation))
        elif kind == "model_routing":
            routing.append((case, observation))
        else:  # pragma: no cover - closed by _case_kind
            raise AssertionError(kind)

    if not identity or not malicious or not valid_reads or not connector or not routing:
        raise ValueError("operational corpus must cover every release metric family")

    verified = [item for _, item in identity if _boolean(item, "verified")]
    deterministic = [
        (_identity_correct(case, item))
        for case, item in identity
        if bool(case.get("deterministic_fixture"))
    ]
    if not verified or not deterministic:
        raise ValueError("identity observations must include verified and deterministic cases")

    selected = [item for _, item in connector if _boolean(item, "selected")]
    critical = [(case, item) for case, item in connector if bool(case.get("critical"))]
    zero_call = [item for case, item in connector if not bool(case.get("expected_selected", True))]
    if not selected or not critical or not zero_call:
        raise ValueError(
            "connector observations must cover selected, critical, and zero-call cases"
        )

    latency = [
        item
        for case, item in routing
        if case.get("expected_execution_class") == "latency_optimized"
    ]
    reasoning = [
        item
        for case, item in routing
        if case.get("expected_execution_class") == "reasoning_optimized"
    ]
    if not latency or not reasoning:
        raise ValueError("routing observations must cover both execution classes")

    metrics = {
        "verified_identity_precision": _rate(
            sum(_boolean(item, "correct") for item in verified), len(verified)
        ),
        "deterministic_identity_correct_rate": _rate(sum(deterministic), len(deterministic)),
        "malicious_corpus_block_rate": _rate(sum(malicious), len(malicious)),
        "valid_read_corpus_pass_rate": _rate(sum(valid_reads), len(valid_reads)),
        "connector_useful_call_rate": _rate(
            sum(_boolean(item, "useful") for item in selected), len(selected)
        ),
        "connector_missed_critical_rate": _rate(
            sum(not _boolean(item, "selected") for _, item in critical), len(critical)
        ),
        "connector_zero_call_correct_rate": _rate(
            sum(not _boolean(item, "selected") for item in zero_call), len(zero_call)
        ),
        "connector_mean_cost": _mean(_number(item, "cost") for _, item in connector),
        "connector_mean_latency_ms": _mean(_number(item, "latency_ms") for _, item in connector),
        "routing_latency_hit_rate": _rate(
            sum(_string(item, "actual_execution_class") == "latency_optimized" for item in latency),
            len(latency),
        ),
        "routing_unnecessary_reasoning_rate": _rate(
            sum(
                _string(item, "actual_execution_class") == "reasoning_optimized" for item in latency
            ),
            len(latency),
        ),
        "routing_missed_upgrade_rate": _rate(
            sum(
                _string(item, "actual_execution_class") != "reasoning_optimized"
                for item in reasoning
            ),
            len(reasoning),
        ),
        "routing_compression_correct_rate": _rate(
            sum(_boolean(item, "compression_correct") for _, item in routing), len(routing)
        ),
        **_routing_means("latency", latency),
        **_routing_means("reasoning", reasoning),
    }

    failures: list[str] = []
    fixed_minimums = {
        "verified_identity_precision": 0.99,
        "deterministic_identity_correct_rate": 1.0,
        "malicious_corpus_block_rate": 1.0,
        "valid_read_corpus_pass_rate": 0.99,
    }
    for name, minimum in fixed_minimums.items():
        if metrics[name] < minimum:
            failures.append(f"{name}_below_{minimum:g}")

    baseline_metrics = baseline.get("metrics")
    if not isinstance(baseline_metrics, Mapping):
        raise TypeError("operational baseline metrics must be an object")
    for name, direction in _BASELINE_DIRECTIONS.items():
        threshold = _number(baseline_metrics, name)
        value = metrics[name]
        if direction == "minimum" and value < threshold:
            failures.append(f"{name}_below_frozen_baseline")
        elif direction == "maximum" and value > threshold:
            failures.append(f"{name}_above_frozen_baseline")
    return OperationalEvaluation(metrics, not failures, tuple(failures))


def compare_canary(baseline: QualityEvaluation, candidate: QualityEvaluation) -> CanaryEvaluation:
    failures: list[str] = []
    if not baseline.release_gate_passed:
        failures.append("canary_baseline_release_gate_failed")
    higher_is_better = {
        "confirmed_incident_cause_precision",
        "correct_downgrade_rate",
        "confirmed_causal_relation_precision",
        "confirmed_evidence_reference_completeness",
        "confirmed_version_gate_rate",
        "confirmed_counter_evidence_gate_rate",
    }
    for name, candidate_metric in candidate.metrics.items():
        baseline_metric = baseline.metrics[name]
        if name in higher_is_better and candidate_metric.value < baseline_metric.value:
            failures.append(f"{name}_canary_regression")
        if name == "false_confirmed_rate" and candidate_metric.value > baseline_metric.value:
            failures.append("false_confirmed_rate_canary_regression")

    baseline_cases = _case_pass_rates(baseline)
    candidate_cases = _case_pass_rates(candidate)
    if baseline_cases.keys() != candidate_cases.keys():
        raise ValueError("canary baseline and candidate must cover identical frozen cases")
    for case_id, baseline_rate in baseline_cases.items():
        if candidate_cases[case_id] < baseline_rate:
            failures.append(f"case_regression:{case_id}")
    return CanaryEvaluation(not failures, tuple(failures))


def required_baseline_metrics() -> tuple[str, ...]:
    return tuple(_BASELINE_DIRECTIONS)


def _case_kind(case: Mapping[str, Any]) -> str:
    if case.get("kind") in {"identity", "connector_selection", "model_routing"}:
        return str(case["kind"])
    if "language" in case or "content" in case:
        return "security"
    raise ValueError(f"unknown operational case kind: {case.get('case_id')}")


def _identity_correct(case: Mapping[str, Any], observation: Mapping[str, Any]) -> bool:
    verified = _boolean(observation, "verified")
    expected = _string(case, "expected")
    if expected == "verified_correct":
        return verified and _boolean(observation, "correct")
    if expected == "not_verified":
        return not verified
    raise ValueError("identity case expected value is invalid")


def _routing_means(prefix: str, observations: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {
        f"routing_{prefix}_mean_tokens": _mean(
            _number(item, "input_tokens") for item in observations
        ),
        f"routing_{prefix}_mean_cost": _mean(_number(item, "cost") for item in observations),
        f"routing_{prefix}_mean_latency_ms": _mean(
            _number(item, "latency_ms") for item in observations
        ),
    }


def _case_pass_rates(value: QualityEvaluation) -> Mapping[str, float]:
    totals: dict[str, list[bool]] = defaultdict(list)
    for item in value.cases:
        totals[item.case_id].append(item.passed)
    return {case_id: _rate(sum(results), len(results)) for case_id, results in totals.items()}


def _string(value: Mapping[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise TypeError(f"{name} must be a non-empty string")
    return item


def _boolean(value: Mapping[str, Any], name: str) -> bool:
    item = value.get(name)
    if not isinstance(item, bool):
        raise TypeError(f"{name} must be a boolean")
    return item


def _number(value: Mapping[str, Any], name: str) -> float:
    item = value.get(name)
    if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0:
        raise TypeError(f"{name} must be a non-negative number")
    return float(item)


def _rate(successes: int, total: int) -> float:
    if total < 1:
        raise ValueError("rate denominator must be positive")
    return successes / total


def _mean(values) -> float:
    items = tuple(values)
    if not items:
        raise ValueError("mean requires at least one value")
    return sum(items) / len(items)
