#!/usr/bin/env python3
"""Run deterministic analysis quality smoke checks or the strict release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from lode.application.quality_evaluation import (
    evaluate_incidents,
    observation_from_oracle,
    observation_from_result,
)
from lode.application.release_evaluation import (
    compare_canary,
    evaluate_operational,
    required_baseline_metrics,
)

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "evals" / "v1"
_RUN_MANIFEST_FIELDS = frozenset(
    {
        "run_id",
        "provider_account_class",
        "model_deployment",
        "model_deployment_revision",
        "role",
        "execution_class",
        "prompt_revision",
        "schema_revision",
        "policy_revision",
        "corpus_sha256",
    }
)
_RELEASE_BUNDLE_HASH_FIELDS = {
    "candidate_observations_sha256": "observations",
    "candidate_run_manifest_sha256": "run_manifest",
    "operational_observations_sha256": "operational_observations",
    "operational_baseline_sha256": "operational_baseline",
    "canary_baseline_observations_sha256": "canary_baseline_observations",
    "canary_baseline_run_manifest_sha256": "canary_baseline_run_manifest",
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{number} must contain an object")
        records.append(value)
    return records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_manifest(path: Path, corpus_sha256: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("release run manifest must be an object")
    if set(value) != _RUN_MANIFEST_FIELDS:
        raise ValueError("release run manifest must contain the exact frozen fields")
    if value["corpus_sha256"] != corpus_sha256:
        raise ValueError("release run manifest corpus hash does not match the evaluated corpus")
    if any(not isinstance(value[field], str) or not value[field] for field in _RUN_MANIFEST_FIELDS):
        raise TypeError("release run manifest fields must be non-empty strings")
    return value


def _eval_corpus_sha256() -> str:
    manifest = json.loads((EVAL_ROOT / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    for relative in manifest["files"]:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((EVAL_ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _operational_baseline(path: Path, eval_corpus_sha256: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "name",
        "eval_corpus_sha256",
        "provider_account_class",
        "model_deployment",
        "model_deployment_revision",
        "prompt_revision",
        "schema_revision",
        "policy_revision",
        "metrics",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("operational baseline must contain the exact frozen fields")
    if value["schema_version"] != "lode-operational-baseline.v1":
        raise ValueError("operational baseline schema version is invalid")
    if value["eval_corpus_sha256"] != eval_corpus_sha256:
        raise ValueError("operational baseline corpus hash does not match the evaluated corpus")
    for field in required - {"metrics"}:
        if not isinstance(value[field], str) or not value[field]:
            raise TypeError(f"operational baseline {field} must be a non-empty string")
    metrics = value["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != set(required_baseline_metrics()):
        raise ValueError("operational baseline metrics are incomplete or contain unknown metrics")
    for name, metric in metrics.items():
        if isinstance(metric, bool) or not isinstance(metric, (int, float)) or metric < 0:
            raise TypeError(f"operational baseline metric {name} must be non-negative")
    return value


def _release_bundle(
    path: Path,
    *,
    artifacts: Mapping[str, Path],
    gold_corpus_sha256: str,
    eval_corpus_sha256: str,
) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "release_id",
        "gold_corpus_sha256",
        "eval_corpus_sha256",
        *_RELEASE_BUNDLE_HASH_FIELDS,
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("release bundle must contain the exact frozen fields")
    if value["schema_version"] != "lode-release-bundle.v1":
        raise ValueError("release bundle schema version is invalid")
    if not isinstance(value["release_id"], str) or not value["release_id"]:
        raise TypeError("release bundle release_id must be a non-empty string")
    if value["gold_corpus_sha256"] != gold_corpus_sha256:
        raise ValueError("release bundle gold corpus hash does not match")
    if value["eval_corpus_sha256"] != eval_corpus_sha256:
        raise ValueError("release bundle evaluation corpus hash does not match")
    for hash_field, artifact_name in _RELEASE_BUNDLE_HASH_FIELDS.items():
        if value[hash_field] != _sha256(artifacts[artifact_name]):
            raise ValueError(f"release bundle hash does not match {artifact_name}")
    return value


def _incident_observations(path: Path, corpus: list[dict[str, Any]]) -> list:
    observations = [observation_from_result(record) for record in _jsonl(path)]
    expected = {record["case_id"]: record["result_state"] for record in corpus}
    if {item.case_id for item in observations} != expected.keys():
        raise ValueError("release observations must cover every case in the versioned corpus")
    return [
        replace(item, expected_result_state=str(expected[item.case_id])) for item in observations
    ]


def _operational_cases() -> list[dict[str, Any]]:
    return [
        *_jsonl(EVAL_ROOT / "operational-cases.jsonl"),
        *_jsonl(EVAL_ROOT / "security" / "malicious-evidence.jsonl"),
        *_jsonl(EVAL_ROOT / "security" / "native-reads.jsonl"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true", help="enforce statistical release gates")
    parser.add_argument(
        "--observations",
        type=Path,
        help="JSONL model/provider observations; required for a release evaluation",
    )
    parser.add_argument("--operational-observations", type=Path)
    parser.add_argument("--operational-baseline", type=Path)
    parser.add_argument("--canary-baseline-observations", type=Path)
    parser.add_argument("--canary-baseline-run-manifest", type=Path)
    parser.add_argument("--release-bundle", type=Path)
    parser.add_argument(
        "--run-manifest",
        type=Path,
        help="frozen provider/deployment/role/prompt/schema/policy metadata",
    )
    args = parser.parse_args()
    corpus_path = EVAL_ROOT / "gold-incidents.jsonl"
    corpus = _jsonl(corpus_path)
    corpus_sha256 = _sha256(corpus_path)
    manifest = (
        _run_manifest(args.run_manifest, corpus_sha256) if args.run_manifest is not None else None
    )
    if args.observations is not None:
        observations = _incident_observations(args.observations, corpus)
    else:
        observations = [observation_from_oracle(record) for record in corpus]
    result = evaluate_incidents(observations)
    operational = None
    canary = None
    release_bundle = None
    release_paths = (
        args.observations,
        args.run_manifest,
        args.operational_observations,
        args.operational_baseline,
        args.canary_baseline_observations,
        args.canary_baseline_run_manifest,
        args.release_bundle,
    )
    if args.release and any(path is None for path in release_paths):
        raise SystemExit(
            "--release requires candidate, operational, canary baseline, and release bundle inputs"
        )
    if args.release:
        assert all(path is not None for path in release_paths)
        eval_corpus_sha256 = _eval_corpus_sha256()
        operational_baseline = _operational_baseline(args.operational_baseline, eval_corpus_sha256)
        operational = evaluate_operational(
            _operational_cases(),
            _jsonl(args.operational_observations),
            operational_baseline,
        )
        canary_manifest = _run_manifest(args.canary_baseline_run_manifest, corpus_sha256)
        if manifest is None or manifest["run_id"] == canary_manifest["run_id"]:
            raise ValueError("candidate and canary baseline must be distinct frozen runs")
        version_fields = {
            "model_deployment",
            "model_deployment_revision",
            "prompt_revision",
            "schema_revision",
            "policy_revision",
        }
        if all(manifest[field] == canary_manifest[field] for field in version_fields):
            raise ValueError("candidate and canary baseline must differ by a frozen revision")
        canary_baseline = evaluate_incidents(
            _incident_observations(args.canary_baseline_observations, corpus)
        )
        canary = compare_canary(canary_baseline, result)
        artifacts = {
            "observations": args.observations,
            "run_manifest": args.run_manifest,
            "operational_observations": args.operational_observations,
            "operational_baseline": args.operational_baseline,
            "canary_baseline_observations": args.canary_baseline_observations,
            "canary_baseline_run_manifest": args.canary_baseline_run_manifest,
        }
        release_bundle = _release_bundle(
            args.release_bundle,
            artifacts=artifacts,
            gold_corpus_sha256=corpus_sha256,
            eval_corpus_sha256=eval_corpus_sha256,
        )

    payload = {
        "evaluation_mode": "release" if args.release else "smoke",
        "frozen_inputs": {
            "corpus": str(corpus_path.relative_to(ROOT)),
            "corpus_sha256": corpus_sha256,
            "observations": (
                None if args.observations is None else str(args.observations.resolve())
            ),
            "run_manifest": manifest,
        },
        "metrics": {name: asdict(metric) for name, metric in result.metrics.items()},
        "cases": [asdict(item) for item in result.cases],
        "smoke_passed": result.smoke_passed,
        "release_gate_passed": result.release_gate_passed,
        "release_gate_failures": list(result.release_gate_failures),
        "operational": (
            None
            if operational is None
            else {
                "metrics": operational.metrics,
                "passed": operational.passed,
                "failures": list(operational.failures),
            }
        ),
        "canary": (
            None if canary is None else {"passed": canary.passed, "failures": list(canary.failures)}
        ),
        "release_bundle": release_bundle,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if not result.smoke_passed:
        raise SystemExit(1)
    if args.release:
        if (
            not result.release_gate_passed
            or operational is None
            or not operational.passed
            or canary is None
            or not canary.passed
        ):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
