"""Unit tests for human-hint injection and the evidence packet helpers."""

from __future__ import annotations

from lode.engine.runner import (
    _build_prompts,
    _heuristic_packet,
    _normalize_evidence_packet,
)


class _FakeAlert:
    title = "Payment latency"
    level = "CRITICAL"
    error_message = "p99>2s"
    fields = {"orderId": "1"}


def test_human_hints_injected_as_data_not_commands() -> None:
    _system, user = _build_prompts(
        _FakeAlert(),
        None,
        [],
        [],
        None,
        human_hints="- check the payment gateway timeout",
    )
    assert "HUMAN_HINTS" in user
    assert "payment gateway timeout" in user
    # The model is told explicitly not to obey instructions inside the hints.
    assert "not commands" in user


def test_no_human_hints_section_when_none() -> None:
    _system, user = _build_prompts(_FakeAlert(), None, [], [], None)
    assert "HUMAN_HINTS" not in user


def test_evidence_registry_rendered_in_prompt() -> None:
    catalog = [
        {"id": 11, "kind": "git", "locator": "https://x/svc@abc:pay.py:3"},
        {"id": 12, "kind": "git", "locator": "https://x/svc@abc:svc.py:9"},
    ]
    _system, user = _build_prompts(
        _FakeAlert(), None, [], [], None, evidence_catalog=catalog
    )
    assert "EVIDENCE REGISTRY" in user
    assert "[11]" in user and "[12]" in user
    # The model is told to cite IDs that exist.
    assert "evidence_refs" in user


def test_system_prompt_requires_structured_output() -> None:
    system, _user = _build_prompts(_FakeAlert(), None, [], [], None)
    assert "evidence_refs" in system
    assert "facts" in system
    assert "inferences" in system
    assert "unknowns" in system


# --- evidence packet normalization ---------------------------------------
def test_normalize_accepts_well_formed_packet() -> None:
    catalog = [{"id": 1, "kind": "git", "locator": "r@a:p:1"}]
    parsed = {
        "conclusion": "DB connection pool exhausted",
        "confidence": 0.85,
        "evidence_refs": [1],
        "facts": ["pool size=5"],
        "inferences": ["upstream slowdown"],
        "unknowns": ["why now"],
    }
    pkt = _normalize_evidence_packet(parsed, catalog)
    assert pkt is not None
    assert pkt["conclusion"] == "DB connection pool exhausted"
    assert pkt["evidence_refs"] == [1]
    assert pkt["facts"] == ["pool size=5"]


def test_normalize_filters_unknown_evidence_ids() -> None:
    catalog = [{"id": 1, "kind": "git", "locator": "r@a:p:1"}]
    parsed = {
        "conclusion": "x",
        "confidence": 0.5,
        "evidence_refs": [1, 999],  # 999 does not exist in the registry
    }
    pkt = _normalize_evidence_packet(parsed, catalog)
    assert pkt["evidence_refs"] == [1]


def test_normalize_rejects_missing_conclusion() -> None:
    catalog = []
    assert _normalize_evidence_packet({"confidence": 0.5}, catalog) is None
    assert _normalize_evidence_packet("not a dict", catalog) is None


def test_normalize_clamps_confidence() -> None:
    catalog = []
    pkt = _normalize_evidence_packet(
        {"conclusion": "x", "confidence": 5.0}, catalog
    )
    assert pkt["confidence"] == 1.0


def test_heuristic_packet_is_flagged_as_fallback() -> None:
    alert = _FakeAlert()
    conclusion, confidence, refs, facts, inferences, unknowns = _heuristic_packet(
        alert, "deploy notes", None, {}, human_hints=None
    )
    assert refs == []  # heuristic cannot cite artifacts
    assert any("Heuristic" in u for u in unknowns)
    # The deploy context belongs in the facts list, not asserted into the claim.
    assert any("deploy context" in f.lower() for f in facts)

