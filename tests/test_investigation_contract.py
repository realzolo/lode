from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from lode.api.routes.investigations import _diff_view, _operations
from lode.config import settings
from lode.db.models.investigation import EXECUTION_EVENT_PHASES, STAGE_STATUSES, STAGE_TYPES
from lode.engine.investigation_evidence import _context_files
from lode.engine.investigation_runner import _fallback, _parse_packet


def test_canonical_stages_have_no_skipped_compatibility_state() -> None:
    assert STAGE_TYPES == (
        "ingest", "plan", "source", "observability", "dependencies",
        "reasoning", "resolution",
    )
    assert "skipped" not in STAGE_STATUSES
    assert set(STAGE_STATUSES) == {
        "queued", "running", "succeeded", "partial", "blocked", "failed", "not_configured",
    }


def test_chinese_safety_fallback_is_chinese_and_reviewable() -> None:
    conclusion, unknowns, remediation = _fallback("zh")
    assert "证据" in conclusion
    assert "证据" in unknowns[0]
    assert remediation["risk_level"] == "high"
    assert "人工" in remediation["summary"]


def test_reasoning_requires_existing_evidence_reference() -> None:
    assert _parse_packet(
        '{"conclusion":"证据表明存在连接超时。","confidence":0.8,"evidence_refs":[99]}',
        [],
        "zh",
    ) is None


def test_execution_events_group_a_started_and_terminal_fact_without_ui_inference() -> None:
    started = SimpleNamespace(stage_id=7, operation_id="clone", event_type="git_clone", phase="started", collection_id=2, sequence=4, detail={"requested_ref": "abc"}, artifact_refs=[], occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    finished = SimpleNamespace(stage_id=7, operation_id="clone", event_type="git_clone", phase="succeeded", collection_id=2, sequence=5, detail={"resolved_sha": "abc123"}, artifact_refs=[18], occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC))
    operation = _operations([started, finished])[7][0]
    assert EXECUTION_EVENT_PHASES == ("started", "succeeded", "partial", "blocked", "failed", "not_configured")
    assert operation["status"] == "succeeded"
    assert operation["detail"] == {"requested_ref": "abc", "resolved_sha": "abc123"}
    assert operation["artifact_refs"] == [18]


def test_context_file_discovery_uses_only_admin_controlled_patterns(tmp_path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("project context")
    (tmp_path / "AGENTS.md").write_text("bounded instructions")
    (tmp_path / "secrets.txt").write_text("must not be read")
    monkeypatch.setattr(settings, "evidence_git_context_paths", "AGENTS.md,README.md")
    monkeypatch.setattr(settings, "evidence_git_context_max_files", 8)
    assert [path.name for path in _context_files(tmp_path)] == ["AGENTS.md", "README.md"]


def test_archived_diff_is_converted_to_readonly_editor_inputs() -> None:
    view = _diff_view("diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-print('old')\n+print('new')")
    assert view["mode"] == "diff"
    assert "print('old')" in view["before"]
    assert "print('new')" in view["after"]


def test_v2_migration_revision_fits_the_immutable_v1_version_column() -> None:
    migration = Path("alembic/versions/0003_investigation_execution_events.py").read_text()
    assert 'revision = "0003_execution_events"' in migration
    assert len("0003_execution_events") <= 32
