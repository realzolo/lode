from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from lode.api.routes.investigations import _artifact, _current_activity, _diff_view, _event_display, _is_workbench_v2_brief, _operations, _sse
from lode.config import settings
from lode.db.models.investigation import EXECUTION_EVENT_PHASES, NODE_STATUSES, RESULT_STATES
from lode.engine.investigation_graph import _alert_summary, _parse_reasoning, planned_capabilities, validate_plan_dependencies, validate_restricted_tool_input
from lode.engine.evidence.git import derive_query_terms, search_tree
from lode.engine.investigation_evidence import _context_files, _revision_targets
from lode.engine.investigation_runner import _fallback, _parse_packet


def test_capability_catalog_creates_only_authorized_dynamic_nodes() -> None:
    empty = {"repositories": [], "observability": [], "dependencies": [], "data_sources": []}
    assert planned_capabilities(empty) == ("planning", "evidence_request", "reasoning", "remediation")
    redis_only = {"repositories": [], "observability": [], "dependencies": [{"kind": "redis"}], "data_sources": []}
    assert planned_capabilities(redis_only) == ("planning", "dependencies", "reasoning", "remediation")
    source_and_logs = {"repositories": [{"id": 1}], "observability": [{"kind": "loki"}], "dependencies": [], "data_sources": []}
    assert planned_capabilities(source_and_logs) == ("planning", "source", "observability", "reasoning", "remediation")
    assert "database" not in planned_capabilities(empty)
    assert "canceled" in NODE_STATUSES
    assert set(RESULT_STATES) == {"confirmed", "provisional", "insufficient", "unavailable"}


def test_chinese_safety_fallback_is_chinese_and_non_disruptive() -> None:
    conclusion, unknowns, remediation = _fallback("zh")
    assert "证据" in conclusion
    assert "初步" not in conclusion
    assert "证据" in unknowns[0]
    assert remediation["risk_level"] == "low"
    assert "生产变更" in remediation["summary"]


def test_reasoning_requires_existing_evidence_reference() -> None:
    assert _parse_packet(
        '{"conclusion":"证据表明存在连接超时。","confidence":0.8,"evidence_refs":[99]}',
        [],
        "zh",
    ) is None


def test_execution_events_group_started_progress_and_terminal_fact_for_live_console() -> None:
    started = SimpleNamespace(stage_id=7, operation_id="clone", event_type="git_clone", phase="started", collection_id=2, sequence=4, detail={"requested_ref": "abc"}, artifact_refs=[], occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    progress = SimpleNamespace(stage_id=7, operation_id="clone", event_type="git_clone", phase="progress", collection_id=2, sequence=5, detail={"message": "正在读取已授权仓库"}, artifact_refs=[], occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC))
    finished = SimpleNamespace(stage_id=7, operation_id="clone", event_type="git_clone", phase="succeeded", collection_id=2, sequence=6, detail={"resolved_sha": "abc123"}, artifact_refs=[18], occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC))
    operation = _operations([started, progress, finished])[7][0]
    assert EXECUTION_EVENT_PHASES == ("started", "progress", "succeeded", "partial", "blocked", "failed", "not_configured", "canceled")
    assert operation["status"] == "succeeded"
    assert operation["detail"] == {"requested_ref": "abc", "message": "正在读取已授权仓库", "resolved_sha": "abc123"}
    assert operation["artifact_refs"] == [18]
    assert operation["display"]["headline"] == "读取只读仓库"


def test_live_activity_uses_the_latest_unfinished_operation_and_safe_display() -> None:
    completed = SimpleNamespace(node_id=2, operation_id="checkout", event_type="git_checkout", phase="succeeded", collection_id=2, sequence=4, detail={}, artifact_refs=[], occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    active = SimpleNamespace(node_id=3, operation_id="search", event_type="source_search", phase="progress", collection_id=2, sequence=5, detail={"message": "search token=secret-value"}, artifact_refs=[7], occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC))
    activity = _current_activity([completed, active], {2: "node-checkout", 3: "node-search"}, "zh")
    assert activity is not None
    assert activity["operation_id"] == "search"
    assert activity["node_id"] == "node-search"
    assert activity["is_running"] is True
    assert activity["display"]["actor"] == "collector"
    assert "secret-value" not in activity["display"]["message"]


def test_context_file_discovery_uses_only_admin_controlled_patterns(tmp_path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("project context")
    (tmp_path / "AGENTS.md").write_text("bounded instructions")
    (tmp_path / "secrets.txt").write_text("must not be read")
    monkeypatch.setattr(settings, "evidence_git_context_paths", "AGENTS.md,README.md")
    monkeypatch.setattr(settings, "evidence_git_context_max_files", 8)
    assert [path.name for path in _context_files(tmp_path)] == ["AGENTS.md", "README.md"]


def test_source_search_prioritizes_exact_contract_terms_and_ignores_project_docs(tmp_path) -> None:
    alert = SimpleNamespace(
        title="Payment order creation failed",
        error_message="Payment creation failed",
        fields={"gatewayCode": "PAYMENT_FAILED", "providerCode": "Passion"},
    )
    (tmp_path / "README.md").write_text("PAYMENT_FAILED is a generic project note")
    (tmp_path / "generic.py").write_text("# payment creation failed\nreturn None\n")
    (tmp_path / "gateway.py").write_text(
        "def create_order():\n    response = client.charge()\n    if response.code == 'PAYMENT_FAILED':\n        raise PaymentError('Passion rejected')\n"
    )
    terms = derive_query_terms(alert)
    assert terms[:2] == ["PAYMENT_FAILED", "Passion"]
    hits = search_tree(tmp_path, terms, max_files=1, max_bytes=50_000)
    assert [hit["path"] for hit in hits] == ["gateway.py"]


def test_source_collection_does_not_duplicate_default_branch_without_incident_revision() -> None:
    assert _revision_targets(None, "main") == [("latest", "main")]
    assert _revision_targets("a" * 40, "main") == [("incident", "a" * 40), ("latest", "main")]


def test_archived_diff_is_converted_to_readonly_editor_inputs() -> None:
    view = _diff_view("diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-print('old')\n+print('new')")
    assert view["mode"] == "diff"
    assert "print('old')" in view["before"]
    assert "print('new')" in view["after"]


def test_v2_migration_revision_fits_the_immutable_v1_version_column() -> None:
    migration = Path("alembic/versions/0003_investigation_execution_events.py").read_text()
    assert 'revision = "0003_execution_events"' in migration
    assert len("0003_execution_events") <= 32


def test_dynamic_graph_migration_preserves_auditable_node_contract() -> None:
    migration = Path("alembic/versions/0004_dynamic_investigation_graph.py").read_text()
    assert 'revision = "0004_dynamic_graph"' in migration
    assert "investigation_plan_nodes" in migration
    assert "investigation_ai_invocations" in migration
    assert "investigation_evidence_links" in migration
    assert "operator_input" in migration
    assert "canceled" in migration


def test_dynamic_plan_rejects_cycles_and_unregistered_executable_input() -> None:
    with __import__("pytest").raises(ValueError, match="cycle"):
        validate_plan_dependencies({"source": ["reasoning"], "reasoning": ["source"]})
    with __import__("pytest").raises(ValueError, match="executable"):
        validate_restricted_tool_input("source", {"repositories": [1], "command": "git status"})
    validate_restricted_tool_input("source", {"repositories": [1], "steps": ["search_source"]})


def test_v2_planning_defers_evidence_request_until_it_is_discriminating() -> None:
    catalog = {"repositories": [{"id": 1}], "observability": [], "dependencies": [], "data_sources": []}
    assert planned_capabilities(catalog) == ("planning", "source", "reasoning", "remediation")
    assert planned_capabilities({"repositories": [], "observability": [], "dependencies": [], "data_sources": []}) == ("planning", "evidence_request", "reasoning", "remediation")


def test_v2_reasoning_rejects_uncited_structured_claims() -> None:
    artifact = SimpleNamespace(id=7)
    packet, error = _parse_reasoning(
        '{"conclusion":"支付网关返回失败。","confidence":0.8,"evidence_refs":[7],"facts":[{"text":"存在未引用的事实"}]}',
        [artifact],
        "zh",
    )
    assert packet is None
    assert error == "invalid_citation"


def test_v2_reasoning_requires_a_cited_engineering_brief() -> None:
    artifact = SimpleNamespace(id=7, source_kind="loki", metadata_={})
    packet, error = _parse_reasoning(
        '{"conclusion":"支付网关返回失败。","confidence":0.8,"evidence_refs":[7],"brief":{"headline":"支付网关调用失败","summary":"已确认网关返回失败。","direct_cause":{"status":"confirmed","text":"上游网关明确拒绝了支付请求。","evidence_refs":[7]},"confirmed":[{"text":"网关返回 PAYMENT_FAILED。","evidence_refs":[7]}],"uncertain":["尚缺少事故时间窗内的调用链。"],"next_step":"补充对应 trace。"}}',
        [artifact],
        "zh",
    )
    assert error is None
    assert packet is not None
    assert packet["brief"]["confirmed"][0]["evidence_refs"] == [7]
    assert packet["brief"]["direct_cause"]["status"] == "confirmed"

    packet, error = _parse_reasoning(
        '{"conclusion":"支付网关返回失败。","confidence":0.8,"evidence_refs":[7],"brief":{"headline":"支付网关调用失败","summary":"已确认网关返回失败。","direct_cause":{"status":"confirmed","text":"上游网关明确拒绝了支付请求。","evidence_refs":[]},"confirmed":[{"text":"网关返回 PAYMENT_FAILED。","evidence_refs":[7]}],"uncertain":[],"next_step":"补充对应 trace。"}}',
        [artifact],
        "zh",
    )
    assert packet is None
    assert error == "invalid_direct_cause"

    context_artifact = SimpleNamespace(id=8, metadata_={"role": "repository_context"})
    packet, error = _parse_reasoning(
        '{"conclusion":"支付网关返回失败。","confidence":0.8,"evidence_refs":[8],"brief":{"headline":"支付网关调用失败","summary":"已确认网关返回失败。","direct_cause":{"status":"confirmed","text":"README 说明了支付网关实现。","evidence_refs":[8]},"confirmed":[{"text":"网关返回 PAYMENT_FAILED。","evidence_refs":[8]}],"uncertain":[],"next_step":"补充对应 trace。"}}',
        [context_artifact],
        "zh",
    )
    assert packet is None
    assert error == "invalid_direct_cause"

    reference_artifact = SimpleNamespace(id=9, source_kind="git", metadata_={"role": "latest"})
    packet, error = _parse_reasoning(
        '{"conclusion":"支付网关返回失败。","confidence":0.8,"evidence_refs":[9],"brief":{"headline":"支付网关调用失败","summary":"已确认网关返回失败。","direct_cause":{"status":"confirmed","text":"默认分支代码会抛出该错误。","evidence_refs":[9]},"confirmed":[{"text":"默认分支存在对应代码。","evidence_refs":[9]}],"uncertain":[],"next_step":"补充事故版本。"}}',
        [reference_artifact],
        "zh",
    )
    assert packet is None
    assert error == "insufficient_direct_cause_evidence"

    packet, error = _parse_reasoning(
        '{"conclusion":"支付网关返回失败。","confidence":0.8,"evidence_refs":[7]}',
        [artifact],
        "zh",
    )
    assert packet is None
    assert error == "invalid_brief"


def test_workbench_rejects_a_brief_that_is_not_the_strict_v2_shape() -> None:
    valid = {
        "headline": "支付调用失败",
        "summary": "正在核验事故版本。",
        "direct_cause": {"status": "not_proven", "text": "尚无事故版本证据。", "evidence_refs": []},
        "confirmed": [],
        "impact": [],
        "uncertain": [],
        "next_step": {"text": "补充部署版本。", "evidence_refs": []},
    }
    assert _is_workbench_v2_brief(valid)
    assert not _is_workbench_v2_brief({key: value for key, value in valid.items() if key != "direct_cause"})


def test_source_code_contract_requires_a_complete_new_anchor() -> None:
    source = SimpleNamespace(
        id=9,
        artifact_type="source_file",
        source_kind="git",
        locator="repo@sha:app.py:42",
        content_hash="hash",
        redacted_excerpt="return failure",
        metadata_={"language": "python", "path": "app.py", "sha": "a" * 40, "line": 42, "snippet_start_line": 37, "snippet_end_line": 48},
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert _artifact(source)["code"]["anchor"]["match_line"] == 42

    source.metadata_ = {"language": "python", "path": "app.py", "sha": "a" * 40, "line": 42}
    assert "code" not in _artifact(source)


def test_v2_deterministic_conclusion_is_authoritative_failure_boundary() -> None:
    alert = SimpleNamespace(error_message='{"code":"PAYMENT_FAILED"}', fields={"gatewayCode": "PAYMENT_FAILED", "providerCode": "Passion"})
    conclusion, requirements = _alert_summary(alert, "zh")
    assert conclusion.startswith("调查结论：")
    assert "初步" not in conclusion
    assert requirements


def test_sse_frame_has_replay_cursor_and_safe_payload() -> None:
    frame = _sse("investigation_event", {"sequence": 9, "type": "node_changed"}, 9)
    assert "event: investigation_event" in frame
    assert "id: 9" in frame
    assert '"type":"node_changed"' in frame


def test_realtime_v2_migration_records_conclusion_and_reasoning_contracts() -> None:
    migration = Path("alembic/versions/0005_realtime_investigation_v2.py").read_text()
    assert 'revision = "0005_realtime_investigation_v2"' in migration
    assert "conclusion_version" in migration
    assert "investigation_finding_edges" in migration
    assert "change_set" in migration
    assert "'conclude'" in migration


def test_live_progress_migration_extends_the_strict_event_contract() -> None:
    migration = Path("alembic/versions/0006_investigation_live_progress.py").read_text()
    assert 'revision = "0006_investigation_live_progress"' in migration
    assert "'progress'" in migration
