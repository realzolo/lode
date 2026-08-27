#!/usr/bin/env python3
"""Run deterministic analysis quality smoke checks or the strict release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from lode.application.quality_evaluation import (
    evaluate_incidents,
    observation_from_oracle,
    observation_from_result,
)

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "evals" / "v1"
_RUN_MANIFEST_FIELDS = frozenset(
    {
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
    missing = _RUN_MANIFEST_FIELDS - value.keys()
    if missing:
        raise ValueError(f"release run manifest is missing fields: {sorted(missing)}")
    if value["corpus_sha256"] != corpus_sha256:
        raise ValueError("release run manifest corpus hash does not match the evaluated corpus")
    if any(not isinstance(value[field], str) or not value[field] for field in _RUN_MANIFEST_FIELDS):
        raise TypeError("release run manifest fields must be non-empty strings")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true", help="enforce statistical release gates")
    parser.add_argument(
        "--observations",
        type=Path,
        help="JSONL model/provider observations; required for a release evaluation",
    )
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
        observations = [observation_from_result(record) for record in _jsonl(args.observations)]
        expected = {record["case_id"]: record["result_state"] for record in corpus}
        observed_cases = {item.case_id for item in observations}
        if observed_cases != expected.keys():
            raise ValueError("release observations must cover every case in the versioned corpus")
        observations = [
            replace(item, expected_result_state=str(expected[item.case_id]))
            for item in observations
        ]
    else:
        observations = [observation_from_oracle(record) for record in corpus]
    result = evaluate_incidents(observations)
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
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if not result.smoke_passed:
        raise SystemExit(1)
    if args.release:
        if args.observations is None or manifest is None:
            raise SystemExit(
                "--release requires --observations and --run-manifest from a frozen provider run"
            )
        if not result.release_gate_passed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
