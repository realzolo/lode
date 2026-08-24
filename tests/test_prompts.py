"""Evidence-only prompt and structured-claim contract tests."""

from __future__ import annotations

import pytest
from lode.engine.runner import _build_prompts, _heuristic_packet, _normalize_evidence_packet
from lode.engine.tools import persist_context_evidence
from lode.db.models.intake import EvidenceArtifact


class _Alert:
    id = 7
    title = "Payment latency"
    level = "CRITICAL"
    topic = "alerts.payment"
    error_message = "p99>2s"
    fields = {"orderId": "1"}
    received_at = None


CATALOG = [{"id": 11, "kind": "alert", "locator": "alert://7", "excerpt": "redacted alert", "time_scope": "incident_event"}]


def test_prompt_contains_only_registry_evidence_and_requires_claim_citations() -> None:
    system, user = _build_prompts(CATALOG, output_language="zh")
    assert "redacted alert" in user
    assert "Payment latency" not in user
    assert "Every conclusion, fact, and inference" in system
    assert "Simplified Chinese" in system


def test_normalizer_drops_uncited_claims_and_rejects_uncited_conclusion() -> None:
    packet = _normalize_evidence_packet({
        "conclusion": {"text": "Pool exhaustion", "evidence_refs": [11]},
        "confidence": 0.9,
        "facts": [
            {"text": "Observed timeout", "evidence_refs": [11]},
            {"text": "Unsupported assertion", "evidence_refs": [999]},
        ],
        "inferences": [{"text": "Likely saturation", "evidence_refs": [11]}],
        "unknowns": ["Need historical metrics"],
    }, CATALOG)
    assert packet is not None
    assert packet["conclusion_claim"]["evidence_refs"] == [11]
    assert len(packet["facts"]) == 1
    assert _normalize_evidence_packet({"conclusion": {"text": "x", "evidence_refs": []}}, CATALOG) is None


def test_heuristic_is_low_confidence_and_cites_the_alert_artifact() -> None:
    conclusion, confidence, refs, claim, facts, inferences, unknowns = _heuristic_packet(
        _Alert(), evidence_catalog=CATALOG, output_language="en"
    )
    assert confidence <= 0.2
    assert refs == [11] and claim["evidence_refs"] == [11]
    assert facts[0]["evidence_refs"] == [11]
    assert not inferences
    assert unknowns


def test_heuristic_redacts_alert_error_before_export() -> None:
    alert = _Alert()
    alert.error_message = "database token=really-secret-value"
    _conclusion, _confidence, _refs, _claim, facts, _inferences, _unknowns = _heuristic_packet(
        alert, evidence_catalog=CATALOG, output_language="en"
    )
    assert "really-secret-value" not in facts[0]["text"]


def test_heuristic_without_artifact_refuses_root_cause() -> None:
    conclusion, confidence, refs, _claim, _facts, _inferences, _unknowns = _heuristic_packet(
        _Alert(), evidence_catalog=[]
    )
    assert "insufficient" in conclusion.lower()
    assert confidence == 0.1 and refs == []


class _EvidenceSession:
    def __init__(self):
        self.added = []

    def add_all(self, items):
        self.added.extend(items)

    async def flush(self):
        for index, artifact in enumerate(self.added, start=1):
            artifact.id = index


@pytest.mark.asyncio
async def test_context_inputs_become_redacted_citable_artifacts() -> None:
    session = _EvidenceSession()
    entries = await persist_context_evidence(
        session, analysis_id=3, alert=_Alert(), deploy_description="token=very-secret"
    )
    assert {entry["kind"] for entry in entries} == {"alert", "deploy"}
    assert all(entry["id"] for entry in entries)
    artifacts = [item for item in session.added if isinstance(item, EvidenceArtifact)]
    assert all(artifact.content_hash for artifact in artifacts)
    assert "very-secret" not in (artifacts[1].redacted_excerpt or "")
