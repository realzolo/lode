"""Frozen contract fixture tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lode.contracts.checks import CONTRACT_ROOT, ROOT, validate_contracts, validate_eval_corpus
from lode.contracts.schema_check import expected_schema


def _load(relative: str) -> dict:
    return json.loads((CONTRACT_ROOT / relative).read_text(encoding="utf-8"))


def test_contract_manifest_is_complete_and_deterministic() -> None:
    first = validate_contracts()
    second = validate_contracts()

    assert first == second
    assert first["count"] == 8
    assert first["endpoint_count"] >= 40
    assert first["table_count"] >= 60
    assert len(first["sha256"]) == 64


def test_incident_alert_fixture_is_the_only_final_wire_contract() -> None:
    schema = _load("kafka/incident-alert.schema.json")
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert properties["schema_version"] == {"const": "incident.alert.v1"}
    assert properties["trace_id"] == {"type": "string"}
    assert properties["source_revision"]["pattern"] == "^[0-9a-f]{40}$"
    assert set(schema["required"]) == set(properties)
    removed = {
        "service" + "_name",
        "environment",
        "request" + "_id",
        "git" + "_commit",
    }
    assert removed.isdisjoint(properties)

    error = schema["$defs"]["error"]
    assert error["additionalProperties"] is False
    assert error["properties"]["cause"]["oneOf"][1] == {"$ref": "#/$defs/error"}


def test_native_read_languages_and_payload_shapes_are_closed() -> None:
    schema = _load("evidence/native-read-candidate.schema.json")
    languages = schema["properties"]["language"]["enum"]

    assert languages == [
        "logql",
        "elasticsearch_query_dsl",
        "opensearch_query_dsl",
        "sql",
        "https",
        "command",
    ]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["evidence_anchors"]["minItems"] == 1
    assert schema["$defs"]["commandPayload"]["properties"]["argv"]["type"] == "array"
    assert "command" not in schema["$defs"]["commandPayload"]["properties"]


def test_decision_wave_and_terminal_report_contracts_match_v1() -> None:
    decision = _load("ai/investigation-decision.schema.json")
    report = _load("ai/investigation-report.schema.json")

    assert decision["properties"]["operations"]["maxItems"] == 4
    assert decision["properties"]["decision"]["enum"] == ["continue", "finish"]
    assert report["properties"]["result_state"]["enum"] == [
        "confirmed",
        "hypothesis",
        "insufficient",
        "unavailable",
    ]
    assert "incident_cause" in report["required"]
    assert "code_diagnosis" in report["required"]
    assert "source_assessments" in report["required"]


def test_control_plane_freezes_portfolio_and_context_objects() -> None:
    definitions = _load("control-plane/entities.schema.json")["$defs"]

    assert set(definitions) == {
        "workspace",
        "aiProviderAccount",
        "modelDeployment",
        "workspaceModelBinding",
        "modelPolicyRevision",
        "contextBundleRevision",
    }
    binding = definitions["workspaceModelBinding"]
    assert binding["properties"]["execution_classes"]["minItems"] == 1
    assert binding["properties"]["allowed_roles"]["minItems"] == 1
    assert binding["properties"]["max_context_utilization"]["exclusiveMaximum"] == 1


def test_contract_check_cli_output_is_reproducible() -> None:
    command = [sys.executable, str(ROOT / "scripts" / "check_contracts.py")]
    first = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    second = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)

    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["contracts"]["count"] == 8


def test_eval_corpus_has_a_repeatable_complete_oracle() -> None:
    result = validate_eval_corpus()

    assert result["count"] == 29
    assert result["baseline"] == "phase0-deterministic-oracle"
    assert len(result["sha256"]) == 64


def test_api_and_database_manifests_exclude_removed_resources() -> None:
    api = _load("api/endpoints.json")
    tables = _load("database/tables.json")
    paths = {path for _, path in api["endpoints"]}
    inventory = set(tables["control_plane"] + tables["intake"] + tables["investigation"])

    removed_prefixes = ("/" + "applications", "/" + "services")
    assert all(not path.startswith(removed_prefixes) for path in paths)
    assert set(tables["forbidden_tables"]).isdisjoint(inventory)
    assert "workspaces" in inventory
    assert "native_read_candidates" in inventory


def test_database_invariant_contract_covers_only_frozen_tables() -> None:
    inventory, triggers = expected_schema()

    assert len(inventory) == 67
    assert sum(len(names) for names in triggers.values()) == 83
    assert "trg_evidence_read_attempts_immutable" in triggers["evidence_read_attempts"]
    assert "trg_investigation_reports_semantics" in triggers["investigation_reports"]


def test_all_contract_files_are_utf8_json_objects() -> None:
    manifest = _load("contract-manifest.json")
    for relative in manifest["files"]:
        path = CONTRACT_ROOT / relative
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict), Path(relative)
