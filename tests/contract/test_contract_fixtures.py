"""Frozen contract fixture tests."""

from __future__ import annotations

import hashlib
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
    assert first["count"] == 10
    assert first["endpoint_count"] >= 40
    assert first["table_count"] >= 80
    assert len(first["sha256"]) == 64


def test_incident_alert_fixture_is_the_only_final_wire_contract() -> None:
    path = CONTRACT_ROOT / "kafka" / "incident-alert.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    properties = schema["properties"]

    assert schema["additionalProperties"] is False
    assert properties["schema_version"] == {"const": "incident.alert.v1"}
    assert properties["trace_id"] == {"type": "string"}
    assert properties["source_revision"]["pattern"] == "^[0-9a-f]{40}$"
    assert set(schema["required"]) == {
        "schema_version",
        "alert_id",
        "occurred_at",
        "severity",
        "event",
        "trace_id",
        "source_revision",
        "error",
    }
    removed = {
        "service" + "_name",
        "request" + "_id",
        "git" + "_commit",
        "source_event_id",
        "dedup_key",
        "event_kind",
        "component",
        "environment",
    }
    assert removed.isdisjoint(properties)

    error = schema["$defs"]["error"]
    assert error["additionalProperties"] is False
    assert error["properties"]["cause"]["oneOf"][1] == {"$ref": "#/$defs/error"}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "5601797eb5b75c0f81648ac7d93006d3c2771dfb679213b59b5f79f1286f4e65"
    )


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
    assert decision["properties"]["hypotheses"]["minItems"] == 1
    assert decision["properties"]["decision"]["enum"] == ["continue", "finish"]
    operation = decision["$defs"]["operation"]
    assert decision["title"] == "investigation-decision.v1"
    assert report["title"] == "investigation-report.v1"
    assert "native_candidate" not in operation["properties"]
    assert "native_candidate_json" not in operation["properties"]
    assert "source_query" in operation["required"]
    assert set(decision["$defs"]["sourceQuery"]["required"]) == {
        "terms",
        "symbols",
        "path_hints",
        "evidence_refs",
    }
    native_query = _load("ai/native-query.schema.json")
    resource_analysis = _load("ai/resource-analysis.schema.json")
    assert native_query["title"] == "native-query.v1"
    assert set(native_query["properties"]) == {"payload_json"}
    assert resource_analysis["title"] == "resource-analysis.v1"
    assert report["properties"]["result_state"]["enum"] == [
        "confirmed",
        "hypothesis",
        "insufficient",
        "unavailable",
    ]
    assert "impact_scope" in report["required"]
    assert "causal_graph" in report["required"]
    assert "action_recommendations" in report["required"]
    assert "source_assessments" in report["required"]


def test_control_plane_freezes_portfolio_and_context_objects() -> None:
    definitions = _load("control-plane/entities.schema.json")["$defs"]

    assert set(definitions) == {
        "workspace",
        "workspaceArchitectureContextRevision",
        "workspaceReadiness",
        "repositoryBinding",
        "repositoryAnalysisJob",
        "platformSettings",
        "aiProviderAccount",
        "providerAccountModel",
        "workspaceModelBinding",
        "modelPolicyRevision",
        "contextBundleRevision",
    }
    binding = definitions["workspaceModelBinding"]
    assert binding["properties"]["execution_classes"]["minItems"] == 1
    assert binding["properties"]["allowed_roles"]["minItems"] == 1
    assert binding["properties"]["max_context_utilization"]["exclusiveMaximum"] == 1
    repository = definitions["repositoryBinding"]
    assert repository["properties"]["analysis_mode"]["enum"] == ["code", "documentation"]
    assert repository["properties"]["is_alert_source"] == {"type": "boolean"}


def test_contract_check_cli_output_is_reproducible() -> None:
    command = [sys.executable, str(ROOT / "scripts" / "check_contracts.py")]
    first = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    second = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)

    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["contracts"]["count"] == 10


def test_eval_corpus_has_a_repeatable_complete_oracle() -> None:
    result = validate_eval_corpus()

    assert result["count"] == 123
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

    assert len(inventory) == 87
    assert sum(len(names) for names in triggers.values()) == 94
    assert "trg_incident_signals_immutable" in triggers["incident_signals"]
    assert "trg_incident_signal_links_workspace" in triggers["incident_signal_links"]
    assert "trg_evidence_read_attempts_immutable" in triggers["evidence_read_attempts"]
    assert "trg_investigation_reports_semantics" in triggers["investigation_reports"]
    assert "trg_investigation_signal_inputs_consistency" in triggers[
        "investigation_signal_inputs"
    ]


def test_all_contract_files_are_utf8_json_objects() -> None:
    manifest = _load("contract-manifest.json")
    for relative in manifest["files"]:
        path = CONTRACT_ROOT / relative
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict), Path(relative)
