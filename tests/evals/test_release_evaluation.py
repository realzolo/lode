"""Complete operational and canary release-gate tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from lode.application.quality_evaluation import IncidentObservation, evaluate_incidents
from lode.application.release_evaluation import (
    compare_canary,
    evaluate_operational,
    required_baseline_metrics,
)

ROOT = Path(__file__).parents[2]
EVAL_ROOT = ROOT / "evals" / "v1"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _cases() -> list[dict]:
    return [
        *_jsonl(EVAL_ROOT / "operational-cases.jsonl"),
        *_jsonl(EVAL_ROOT / "security" / "malicious-evidence.jsonl"),
        *_jsonl(EVAL_ROOT / "security" / "native-reads.jsonl"),
    ]


def _observations() -> list[dict]:
    values = []
    for case in _cases():
        common = {
            "observation_id": f"observation:{case['case_id']}",
            "case_id": case["case_id"],
        }
        if case.get("kind") == "identity":
            values.append(
                {
                    **common,
                    "verified": case["expected"] == "verified_correct",
                    "correct": True,
                }
            )
        elif case.get("kind") == "connector_selection":
            selected = bool(case["expected_selected"])
            values.append(
                {
                    **common,
                    "selected": selected,
                    "useful": selected,
                    "cost": 1.0 if selected else 0.0,
                    "latency_ms": 10 if selected else 1,
                }
            )
        elif case.get("kind") == "model_routing":
            values.append(
                {
                    **common,
                    "actual_execution_class": case["expected_execution_class"],
                    "compression_correct": True,
                    "input_tokens": 100,
                    "cost": 0.1,
                    "latency_ms": 20,
                }
            )
        else:
            values.append({**common, "actual": case["expected"]})
    return values


def _permissive_baseline() -> dict:
    metrics = {}
    for name in required_baseline_metrics():
        maximum = "mean_" in name or name in {
            "connector_missed_critical_rate",
            "routing_unnecessary_reasoning_rate",
            "routing_missed_upgrade_rate",
        }
        metrics[name] = 1_000_000 if maximum else 0
    return {"metrics": metrics}


def test_operational_release_gate_covers_every_metric_family() -> None:
    result = evaluate_operational(_cases(), _observations(), _permissive_baseline())

    assert result.passed
    assert result.metrics["verified_identity_precision"] == 1
    assert result.metrics["malicious_corpus_block_rate"] == 1
    assert result.metrics["valid_read_corpus_pass_rate"] == 1
    assert result.metrics["connector_zero_call_correct_rate"] == 1
    assert result.metrics["routing_missed_upgrade_rate"] == 0


def test_operational_release_gate_blocks_security_and_baseline_regressions() -> None:
    observations = _observations()
    malicious = next(item for item in observations if item["case_id"] == "command-shell")
    malicious["actual"] = "allow"
    connector = next(
        item for item in observations if item["case_id"] == "connector-useful-runtime-read"
    )
    connector["useful"] = False
    baseline = _permissive_baseline()
    baseline["metrics"]["connector_useful_call_rate"] = 1.0

    result = evaluate_operational(_cases(), observations, baseline)

    assert not result.passed
    assert "malicious_corpus_block_rate_below_1" in result.failures
    assert "connector_useful_call_rate_below_frozen_baseline" in result.failures


def _incident_corpus(*, cause_correct: bool = True):
    return tuple(
        IncidentObservation(
            case_id="confirmed-source",
            observation_id=f"confirmed:{index}",
            expected_result_state="confirmed",
            actual_result_state="confirmed",
            cause_correct=cause_correct,
            causal_relation_correct=True,
            evidence_references_complete=True,
            version_gate_satisfied=True,
            counter_evidence_gate_satisfied=True,
        )
        for index in range(73)
    ) + tuple(
        IncidentObservation(
            case_id="abstention",
            observation_id=f"abstention:{index}",
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


def test_canary_comparison_blocks_metric_regression() -> None:
    baseline = evaluate_incidents(_incident_corpus())
    candidate = evaluate_incidents(_incident_corpus(cause_correct=False))

    comparison = compare_canary(baseline, candidate)

    assert not comparison.passed
    assert "confirmed_incident_cause_precision_canary_regression" in comparison.failures


def test_canary_comparison_accepts_non_regressing_distinct_run_results() -> None:
    value = evaluate_incidents(_incident_corpus())

    comparison = compare_canary(value, value)

    assert comparison.passed


def _incident_records(prefix: str) -> list[dict]:
    cases = _jsonl(EVAL_ROOT / "gold-incidents.jsonl")
    records = []
    for case in cases:
        repeats = 73 if case["result_state"] == "confirmed" else 20
        for index in range(repeats):
            confirmed = case["result_state"] == "confirmed"
            records.append(
                {
                    "observation_id": f"{prefix}:{case['case_id']}:{index}",
                    "case_id": case["case_id"],
                    "expected_result_state": case["result_state"],
                    "actual_result_state": case["result_state"],
                    "cause_correct": True,
                    "causal_relation_correct": True,
                    "evidence_references_complete": True,
                    "version_gate_satisfied": True,
                    "counter_evidence_gate_satisfied": True,
                    "evidence_refs": [1] if confirmed else [],
                }
            )
    return records


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _eval_corpus_sha256() -> str:
    manifest = json.loads((EVAL_ROOT / "manifest.json").read_text())
    digest = hashlib.sha256()
    for relative in manifest["files"]:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((EVAL_ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_complete_release_bundle_cli_is_executable_and_tamper_evident(tmp_path: Path) -> None:
    candidate_observations = tmp_path / "candidate-observations.jsonl"
    canary_observations = tmp_path / "canary-observations.jsonl"
    operational_observations = tmp_path / "operational-observations.jsonl"
    candidate_manifest = tmp_path / "candidate-run.json"
    canary_manifest = tmp_path / "canary-run.json"
    operational_baseline = tmp_path / "operational-baseline.json"
    release_bundle = tmp_path / "release-bundle.json"
    _write_jsonl(candidate_observations, _incident_records("candidate"))
    _write_jsonl(canary_observations, _incident_records("canary"))
    _write_jsonl(operational_observations, _observations())

    gold_hash = _sha256(EVAL_ROOT / "gold-incidents.jsonl")
    common_manifest = {
        "provider_account_class": "release-test-provider",
        "provider_account_model": "release-test-model",
        "provider_account_model_revision": "1",
        "role": "complete-pipeline",
        "execution_class": "mixed",
        "schema_revision": "current-report-schema",
        "policy_revision": "current-policy",
        "corpus_sha256": gold_hash,
    }
    _write_json(
        candidate_manifest,
        {**common_manifest, "run_id": "candidate-run", "prompt_revision": "candidate-prompt"},
    )
    _write_json(
        canary_manifest,
        {**common_manifest, "run_id": "canary-run", "prompt_revision": "baseline-prompt"},
    )
    _write_json(
        operational_baseline,
        {
            "schema_version": "lode-operational-baseline.v1",
            "name": "synthetic-mechanism-test-only",
            "eval_corpus_sha256": _eval_corpus_sha256(),
            "provider_account_class": "release-test-provider",
            "provider_account_model": "release-test-model",
            "provider_account_model_revision": "0",
            "prompt_revision": "baseline-prompt",
            "schema_revision": "current-report-schema",
            "policy_revision": "current-policy",
            **_permissive_baseline(),
        },
    )
    artifacts = {
        "candidate_observations_sha256": candidate_observations,
        "candidate_run_manifest_sha256": candidate_manifest,
        "operational_observations_sha256": operational_observations,
        "operational_baseline_sha256": operational_baseline,
        "canary_baseline_observations_sha256": canary_observations,
        "canary_baseline_run_manifest_sha256": canary_manifest,
    }
    _write_json(
        release_bundle,
        {
            "schema_version": "lode-release-bundle.v1",
            "release_id": "synthetic-mechanism-test-only",
            "gold_corpus_sha256": gold_hash,
            "eval_corpus_sha256": _eval_corpus_sha256(),
            **{name: _sha256(path) for name, path in artifacts.items()},
        },
    )
    command = [
        sys.executable,
        str(ROOT / "scripts" / "check_analysis_quality.py"),
        "--release",
        "--observations",
        str(candidate_observations),
        "--run-manifest",
        str(candidate_manifest),
        "--operational-observations",
        str(operational_observations),
        "--operational-baseline",
        str(operational_baseline),
        "--canary-baseline-observations",
        str(canary_observations),
        "--canary-baseline-run-manifest",
        str(canary_manifest),
        "--release-bundle",
        str(release_bundle),
    ]

    passed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert passed.returncode == 0, passed.stderr
    payload = json.loads(passed.stdout)
    assert payload["release_gate_passed"]
    assert payload["operational"]["passed"]
    assert payload["canary"]["passed"]

    candidate_observations.write_text(candidate_observations.read_text() + "\n", encoding="utf-8")
    rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert rejected.returncode != 0
    assert "release bundle hash does not match observations" in rejected.stderr

    _write_jsonl(candidate_observations, _incident_records("candidate"))
    candidate_document = json.loads(candidate_manifest.read_text())
    _write_json(candidate_manifest, {**candidate_document, "legacy_alias": "rejected"})
    rejected_manifest = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert rejected_manifest.returncode != 0
    assert "run manifest must contain the exact frozen fields" in rejected_manifest.stderr
