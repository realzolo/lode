"""Validate and fingerprint the frozen current contract and evaluation fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "contracts" / "v1"
EVAL_ROOT = ROOT / "evals" / "v1"


class FixtureError(RuntimeError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"invalid JSON fixture {path.relative_to(ROOT)}: {exc}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FixtureError(f"cannot read {path.relative_to(ROOT)}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FixtureError(
                f"invalid JSONL fixture {path.relative_to(ROOT)}:{line_number}: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise FixtureError(f"record must be an object: {path.relative_to(ROOT)}:{line_number}")
        records.append(record)
    return records


def _canonical_hash(items: list[Any]) -> str:
    encoded = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_contracts() -> dict[str, Any]:
    manifest = _load_json(CONTRACT_ROOT / "contract-manifest.json")
    files = manifest.get("files")
    if not isinstance(files, list) or files != sorted(files) or len(files) != len(set(files)):
        raise FixtureError("contract manifest files must be sorted and unique")

    documents = []
    for relative in files:
        path = (CONTRACT_ROOT / relative).resolve()
        if CONTRACT_ROOT.resolve() not in path.parents or not path.is_file():
            raise FixtureError(f"contract fixture is missing or escapes root: {relative}")
        document = _load_json(path)
        if not isinstance(document, dict):
            raise FixtureError(f"contract fixture must contain an object: {relative}")
        documents.append({"path": relative, "document": document})

    api = _load_json(CONTRACT_ROOT / "api" / "endpoints.json")
    endpoints = [tuple(item) for item in api.get("endpoints", [])]
    if len(endpoints) != len(set(endpoints)):
        raise FixtureError("API endpoint manifest contains duplicates")
    if any(path.startswith(("/applications", "/services")) for _, path in endpoints):
        raise FixtureError("API endpoint manifest contains a removed resource")

    tables = _load_json(CONTRACT_ROOT / "database" / "tables.json")
    inventory = tables["control_plane"] + tables["intake"] + tables["investigation"]
    if len(inventory) != len(set(inventory)):
        raise FixtureError("database table inventory contains duplicates")
    forbidden = set(tables["forbidden_tables"])
    if forbidden.intersection(inventory):
        raise FixtureError("database table inventory contains a removed table")

    invariants = _load_json(CONTRACT_ROOT / "database" / "invariants.json")
    for field in ("immutable_tables", "archive_readonly_tables", "updated_at_tables"):
        names = invariants.get(field)
        if not isinstance(names, list) or names != sorted(names) or len(names) != len(set(names)):
            raise FixtureError(f"database invariant {field} must be sorted and unique")
        if set(names) - set(inventory):
            raise FixtureError(f"database invariant {field} contains an unknown table")
    required_triggers = invariants.get("required_triggers")
    if not isinstance(required_triggers, dict) or list(required_triggers) != sorted(
        required_triggers
    ):
        raise FixtureError("required database triggers must be a sorted object")
    if set(required_triggers) - set(inventory):
        raise FixtureError("required database triggers contain an unknown table")
    for trigger_names in required_triggers.values():
        if (
            not isinstance(trigger_names, list)
            or trigger_names != sorted(trigger_names)
            or len(trigger_names) != len(set(trigger_names))
        ):
            raise FixtureError("required database trigger names must be sorted and unique")

    return {
        "count": len(documents),
        "sha256": _canonical_hash(documents),
        "endpoint_count": len(endpoints),
        "table_count": len(inventory),
    }


def validate_eval_corpus() -> dict[str, Any]:
    manifest = _load_json(EVAL_ROOT / "manifest.json")
    files = manifest.get("files")
    if not isinstance(files, list) or files != sorted(files) or len(files) != len(set(files)):
        raise FixtureError("evaluation manifest files must be sorted and unique")

    all_records: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for relative in files:
        path = (EVAL_ROOT / relative).resolve()
        if EVAL_ROOT.resolve() not in path.parents or not path.is_file():
            raise FixtureError(f"evaluation fixture is missing or escapes root: {relative}")
        for record in _load_jsonl(path):
            case_id = record.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                raise FixtureError(f"evaluation record has no case_id: {relative}")
            if case_id in case_ids:
                raise FixtureError(f"duplicate evaluation case_id: {case_id}")
            if not isinstance(record.get("expected", record.get("result_state")), str):
                raise FixtureError(f"evaluation record has no expected classification: {case_id}")
            case_ids.add(case_id)
            all_records.append({"file": relative, "record": record})

    baseline = _load_json(EVAL_ROOT / manifest["baseline"])
    metrics = baseline.get("metrics", {})
    required_metrics = (
        "case_id_uniqueness",
        "expected_classification_coverage",
        "security_oracle_determinism",
    )
    if any(metrics.get(name) != 1.0 for name in required_metrics):
        raise FixtureError("Phase 0 deterministic baseline must have complete fixture coverage")

    return {
        "count": len(all_records),
        "sha256": _canonical_hash(all_records),
        "baseline": baseline["name"],
    }


def render_result() -> str:
    result = {
        "contracts": validate_contracts(),
        "evals": validate_eval_corpus(),
    }
    return json.dumps(result, sort_keys=True, indent=2)
