import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from lode.api.routes.investigations import _artifact, _operation, _sse
from lode.db.base import Base
from lode.db.models.investigation import RESULT_STATES
from lode.engine.evidence.git import derive_query_terms, extract_stack_frames, related_symbol_hits, search_tree, stack_hits
from lode.engine.investigation_engine import _synthesis_prompt, validate_code_finding
from lode.engine import investigation_evidence


def _source_artifact(*, role: str = "incident", selection: str = "stack_frame") -> SimpleNamespace:
    is_contract = selection == "alert_contract_candidate"
    return SimpleNamespace(
        id=9,
        artifact_type="source_file",
        source_kind="git",
        source_id=3,
        locator="repo@sha:src/payment.py:10",
        content_hash="hash",
        redacted_excerpt="def charge():\n    return gateway.call()",
        metadata_={
            "repo_id": 3,
            "revision": "a" * 40,
            "revision_role": role,
            "path": "src/payment.py",
            "symbol": "charge",
            "highlight_line": 11,
            "start_line": 10,
            "end_line": 12,
            "language": "python",
            "selection_basis": selection,
            "incident_link": "incident_input:2" if is_contract else "File /app/src/payment.py, line 11, in charge" if selection == "stack_frame" else None,
            "incident_evidence_id": 2 if is_contract else None,
            "incident_contract_terms": ["PAYMENT_FAILED"] if is_contract else [],
        },
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _candidate(*, status: str = "confirmed", role: str = "incident") -> dict:
    return {
        "status": status,
        "artifact_id": 9,
        "repo_id": 3,
        "revision": "a" * 40,
        "revision_role": role,
        "path": "src/payment.py",
        "symbol": "charge",
        "start_line": 10,
        "end_line": 12,
        "issue_type": "unchecked_return",
        "faulty_behavior": "返回值未经检查即被当作成功结果。",
        "why_wrong": "上游失败契约要求检查状态码。",
        "expected_behavior": "失败响应必须转换为领域错误。",
        "trigger_condition": "网关返回 PAYMENT_FAILED。",
        "causal_chain": ["网关失败", "代码继续处理", "最终抛出用户报错"],
        "incident_evidence_refs": [9],
        "supporting_evidence_refs": [9],
        "counter_evidence_refs": [],
        "missing_validation": [],
        "fix_direction": "检查返回状态并保留上游错误。",
        "test_scenario": "覆盖 PAYMENT_FAILED 响应。",
    }


def test_v1_result_states_remove_confidence_maturity() -> None:
    assert RESULT_STATES == ("pending", "confirmed", "hypothesis", "insufficient", "unavailable")


def test_complete_normalized_error_contributes_query_terms() -> None:
    value = SimpleNamespace(
        error_name="GatewayRejected",
        error_message="charge failed",
        error_cause={"code": "PAYMENT_FAILED"},
        error_properties={"provider": "Payssion"},
        fields={"method": "enets_sg"},
    )
    terms = derive_query_terms(value)
    assert terms[0] == "GatewayRejected"
    assert {"Payssion", "PAYMENT_FAILED", "enets_sg", "charge"} <= set(terms[:6])


def test_stack_frames_are_parsed_before_lexical_search(tmp_path: Path) -> None:
    source = tmp_path / "src" / "payment.py"
    source.parent.mkdir()
    source.write_text("def charge():\n    response = gateway.call()\n    return response.value\n\ndef other():\n    pass\n")
    stack = 'Traceback:\n  File "/app/src/payment.py", line 2, in charge\nGatewayRejected'
    assert extract_stack_frames(stack)[0]["line"] == 2
    hit = stack_hits(tmp_path, stack)[0]
    assert hit["path"] == "src/payment.py"
    assert hit["symbol"] == "charge"
    assert hit["snippet_start_line"] == 1
    assert "gateway.call" in hit["snippet"]


def test_lexical_match_is_marked_as_candidate_and_docs_are_excluded(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("PAYMENT_FAILED")
    (tmp_path / "gateway.py").write_text("def charge():\n    if code == 'PAYMENT_FAILED':\n        return None\n")
    hits = search_tree(tmp_path, ["PAYMENT_FAILED"])
    assert [item["path"] for item in hits] == ["gateway.py"]
    assert "not causal proof" in hits[0]["selection_reason"]


def test_lexical_snippet_centers_on_highest_priority_error_identifier(tmp_path: Path) -> None:
    source = tmp_path / "gateway.ts"
    source.write_text(
        "import { Payment } from './types';\n"
        "export default defineEventHandler(async () => {\n"
        "  const response = await createPayment();\n"
        "  if (response.code === 'PAYMENT_FAILED') throw new Error(response.message);\n"
        "});\n"
    )
    hit = search_tree(tmp_path, ["PAYMENT_FAILED", "Payment"])[0]
    assert hit["line"] == 4
    assert hit["symbol"] == "defineEventHandler"


def test_error_branch_expands_called_symbol_definition(tmp_path: Path) -> None:
    source = tmp_path / "gateway.ts"
    source.write_text(
        "function normalizeGatewayError(value: unknown) { return String(value); }\n"
        "export default defineEventHandler(async () => {\n"
        "  if (result.code === 'PAYMENT_FAILED') {\n"
        "    return normalizeGatewayError(result);\n"
        "  }\n"
        "});\n"
    )
    primary = search_tree(tmp_path, ["PAYMENT_FAILED"])[0]
    related = related_symbol_hits(tmp_path, [primary])
    assert related[0]["symbol"] == "normalizeGatewayError"
    assert "function normalizeGatewayError" in related[0]["snippet"]


def test_confirmed_code_finding_requires_exact_incident_revision_and_stack_link() -> None:
    artifact = _source_artifact()
    investigation = SimpleNamespace(deployment_sha="release-2026-08-25")
    revision = SimpleNamespace(role="incident", status="resolved", resolved_sha="a" * 40, repo_id=3)
    finding, error = validate_code_finding(_candidate(), artifacts=[artifact], investigation=investigation, revisions=[revision])
    assert error is None
    assert finding is not None
    assert finding["status"] == "confirmed"
    assert finding["path"] == "src/payment.py"
    assert finding["start_line"] == 10


def test_runtime_revision_can_confirm_when_source_alert_has_no_service_commit() -> None:
    artifact = _source_artifact(role="incident")
    candidate = _candidate(role="incident")
    investigation = SimpleNamespace(deployment_sha=None)
    revision = SimpleNamespace(role="incident", status="resolved", resolved_sha="a" * 40, repo_id=3)
    finding, error = validate_code_finding(candidate, artifacts=[artifact], investigation=investigation, revisions=[revision])
    assert error is None
    assert finding is not None
    assert finding["status"] == "confirmed"


def test_repository_missing_runtime_sha_fails_without_default_branch_fallback(tmp_path: Path, monkeypatch) -> None:
    requested_sha = "6c36658895cb220b66f89f17718a001f3f9f02e4"
    repo = SimpleNamespace(id=2, repo_url="https://example.test/payment-gateway.git", default_branch="main")
    commands: list[list[str]] = []

    def fake_git(command: list[str], **_kwargs) -> str:
        commands.append(command)
        if command[:5] == ["fetch", "--depth", "1", "origin", requested_sha]:
            raise subprocess.CalledProcessError(128, command)
        return ""

    monkeypatch.setattr(investigation_evidence, "_git", fake_git)
    with pytest.raises(subprocess.CalledProcessError):
        investigation_evidence._clone_exact_revision(repo, requested_sha, tmp_path)

    assert ["fetch", "--depth", "1", "origin", "main"] not in commands


def test_unverified_code_semantics_downgrade_a_confirmed_finding() -> None:
    artifact = _source_artifact()
    finding, error = validate_code_finding(
        _candidate(),
        artifacts=[artifact],
        investigation=SimpleNamespace(deployment_sha="release"),
        revisions=[SimpleNamespace(role="incident", status="resolved", resolved_sha="a" * 40, repo_id=3)],
        verified_artifact_ids=set(),
    )
    assert error is None
    assert finding is not None
    assert finding["status"] == "hypothesis"
    assert finding["fix_direction"] == ""
    assert "独立代码语义复核" in finding["missing_validation"][-1]


def test_alert_contract_candidate_needs_incident_citation_and_semantic_verification() -> None:
    source = _source_artifact(selection="alert_contract_candidate")
    incident = SimpleNamespace(
        id=2,
        artifact_type="incident_input",
        source_kind="kafka",
        source_id=1,
        locator="incident-input://run",
        content_hash="incident",
        redacted_excerpt='{"code":"PAYMENT_FAILED"}',
        metadata_={"incident_link": True},
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    candidate = _candidate()
    candidate["incident_evidence_refs"] = [2]
    revision = SimpleNamespace(role="incident", status="resolved", resolved_sha="a" * 40, repo_id=3)

    finding, error = validate_code_finding(
        candidate,
        artifacts=[incident, source],
        investigation=SimpleNamespace(deployment_sha="release"),
        revisions=[revision],
        verified_artifact_ids={9},
    )
    assert error is None
    assert finding is not None
    assert finding["status"] == "confirmed"

    candidate["incident_evidence_refs"] = []
    unlinked, _ = validate_code_finding(
        candidate,
        artifacts=[incident, source],
        investigation=SimpleNamespace(deployment_sha="release"),
        revisions=[revision],
        verified_artifact_ids={9},
    )
    assert unlinked is not None
    assert unlinked["status"] == "hypothesis"


def test_file_reference_without_code_range_is_rejected() -> None:
    candidate = _candidate()
    candidate["symbol"] = None
    candidate["start_line"] = None
    finding, error = validate_code_finding(candidate, artifacts=[_source_artifact()], investigation=SimpleNamespace(deployment_sha="release"), revisions=[])
    assert finding is None
    assert error == "exact_code_location_required"


def test_repository_context_cannot_be_a_code_finding() -> None:
    artifact = _source_artifact(selection="repository_context")
    artifact.source_kind = "git_context"
    finding, error = validate_code_finding(_candidate(), artifacts=[artifact], investigation=SimpleNamespace(deployment_sha="release"), revisions=[])
    assert finding is None
    assert error == "source_artifact_required"


def test_application_context_is_bounded_background_not_an_instruction() -> None:
    context = SimpleNamespace(
        id=12,
        artifact_type="application_context",
        source_kind="application",
        locator="application-context://7/run",
        metadata_={"trust": "untrusted_background"},
        redacted_excerpt='{"entries":[{"content":"orders publish after commit"}]}',
    )
    system, prompt = _synthesis_prompt([context], "en")
    payload = json.loads(prompt)
    assert payload["application_context"] == [
        {
            "evidence_id": 12,
            "excerpt": context.redacted_excerpt,
            "trust": "untrusted_background",
        }
    ]
    assert "不能单独证明" in system


def test_api_code_anchor_and_highlight_are_immutable() -> None:
    payload = _artifact(_source_artifact())
    assert payload["code"]["anchor"]["revision"] == "a" * 40
    assert payload["code"]["highlight_start"] == 2


def test_operation_api_includes_purpose_input_progress_result_and_duration() -> None:
    row = SimpleNamespace(
        public_id="op-1", step_id=2, ordinal=3, kind="source.stack_search", actor="collector",
        title="从报错堆栈定位代码", purpose="打开事故版本代码", input_summary={"stack_present": True},
        status="succeeded", result_summary="命中 charge", metrics={"stack_hits": 1}, evidence_refs=[9],
        failure_code=None, failure_detail=None,
        started_at=datetime(2026, 1, 1, tzinfo=UTC), finished_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
    )
    event = SimpleNamespace(sequence=4, kind="progress", message="已命中", detail={"count": 1}, evidence_refs=[9], occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    payload = _operation(row, [event])
    assert payload["purpose"] == "打开事故版本代码"
    assert payload["input"] == {"stack_present": True}
    assert payload["events"][0]["message"] == "已命中"
    assert payload["result"] == "命中 charge"
    assert payload["duration_ms"] == 2_000


def test_schema_has_one_running_step_and_allows_parallel_operations() -> None:
    step_indexes = {item.name: str(item.dialect_options["postgresql"].get("where")) for item in Base.metadata.tables["investigation_steps"].indexes}
    operation_indexes = {item.name: item for item in Base.metadata.tables["investigation_operations"].indexes}
    assert step_indexes["uq_investigation_steps_running"] == "status = 'running'"
    assert "uq_investigation_operations_running" not in operation_indexes
    assert operation_indexes["ix_investigation_operations_running"].unique is False


def test_fresh_schema_contains_only_v1_investigation_tables() -> None:
    tables = set(Base.metadata.tables)
    assert {"investigation_inputs", "investigation_steps", "investigation_decisions", "investigation_operations", "investigation_operation_events", "investigation_code_findings", "investigation_reports"} <= tables
    assert not {"analyses", "analysis_steps", "investigation_stages", "investigation_plan_nodes", "investigation_plan_revisions"} & tables
    migrations = sorted(item.name for item in Path("alembic/versions").glob("*.py"))
    assert migrations == ["0001_initial.py"]


def test_sse_uses_v1_named_events() -> None:
    frame = _sse("operation.progress", {"sequence": 9, "message": "正在定位"}, 9)
    assert "event: operation.progress" in frame
    assert "id: 9" in frame
    assert '"message":"正在定位"' in frame


def test_legacy_integration_collector_has_no_internal_gather() -> None:
    integration_source = Path("src/lode/engine/integrations.py").read_text()
    assert "asyncio.gather" not in integration_source


def test_engine_recovers_persisted_decisions_and_running_steps() -> None:
    source = Path("src/lode/engine/investigation_engine.py").read_text()
    assert "恢复已持久化但尚未执行的调查决策" in source
    assert 'row.status == "running"' in source
    assert "verified_artifact_ids" in source
