from __future__ import annotations

import pytest

from lode.application.quality_evaluation import (
    IncidentObservation,
    evaluate_incidents,
    wilson_interval,
)


def observation(
    case_id: str,
    expected: str,
    actual: str,
    *,
    correct: bool = True,
) -> IncidentObservation:
    return IncidentObservation(
        case_id=case_id,
        expected_result_state=expected,
        actual_result_state=actual,
        cause_correct=correct,
        causal_relation_correct=correct,
        evidence_references_complete=correct,
        version_gate_satisfied=correct,
        counter_evidence_gate_satisfied=correct,
    )


def test_wilson_interval_exposes_small_sample_uncertainty() -> None:
    lower, upper = wilson_interval(1, 1)

    assert lower == pytest.approx(0.20654931437723745)
    assert upper == pytest.approx(1.0)


def test_release_gate_requires_enough_confirmed_successes_for_confidence() -> None:
    small = evaluate_incidents(
        (observation("confirmed", "confirmed", "confirmed"),)
        + tuple(
            observation(f"hypothesis-{index}", "hypothesis", "hypothesis") for index in range(5)
        )
    )
    large = evaluate_incidents(
        tuple(observation(f"confirmed-{index}", "confirmed", "confirmed") for index in range(73))
        + tuple(
            observation(f"hypothesis-{index}", "hypothesis", "hypothesis") for index in range(100)
        )
    )

    assert small.smoke_passed
    assert not small.release_gate_passed
    assert "confirmed_incident_cause_wilson_lower_below_95_percent" in (small.release_gate_failures)
    assert large.release_gate_passed


def test_release_gate_accepts_repeated_corpus_cases_with_unique_observation_ids() -> None:
    repeated = tuple(
        IncidentObservation(
            case_id="confirmed-source",
            observation_id=f"confirmed-source-run-{index}",
            expected_result_state="confirmed",
            actual_result_state="confirmed",
            cause_correct=True,
            causal_relation_correct=True,
            evidence_references_complete=True,
            version_gate_satisfied=True,
            counter_evidence_gate_satisfied=True,
        )
        for index in range(73)
    ) + tuple(
        IncidentObservation(
            case_id="abstention",
            observation_id=f"abstention-run-{index}",
            expected_result_state="hypothesis",
            actual_result_state="hypothesis",
            cause_correct=True,
            causal_relation_correct=True,
            evidence_references_complete=True,
            version_gate_satisfied=True,
            counter_evidence_gate_satisfied=True,
        )
        for index in range(100)
    )

    result = evaluate_incidents(repeated)

    assert result.release_gate_passed


def test_false_confirmation_and_missing_provenance_block_release() -> None:
    observations = tuple(
        observation(f"confirmed-{index}", "confirmed", "confirmed") for index in range(73)
    ) + (
        observation("false-confirmed", "hypothesis", "confirmed", correct=False),
        observation("abstained", "hypothesis", "hypothesis"),
    )

    result = evaluate_incidents(observations)

    assert not result.smoke_passed
    assert not result.release_gate_passed
    assert "false_confirmed_rate_above_1_percent" in result.release_gate_failures
    assert "confirmed_evidence_reference_completeness_below_100_percent" in (
        result.release_gate_failures
    )
