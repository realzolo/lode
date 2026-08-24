"""Structured remediation and safe Agent prompt contracts."""

from lode.engine.runner import (
    _build_agent_prompt,
    _fallback_remediation,
    _normalize_evidence_packet,
)


CATALOG = [{"id": 1, "kind": "alert", "locator": "alert://1", "excerpt": "timeout", "time_scope": "incident_event"}]


def test_remediation_requires_current_evidence_and_is_bounded() -> None:
    packet = _normalize_evidence_packet({
        "conclusion": {"text": "Pool saturation", "evidence_refs": [1]},
        "confidence": 0.8,
        "remediation": {
            "summary": "Reduce load",
            "risk_level": "medium",
            "evidence_refs": [1, 999],
            "preconditions": ["check"],
            "steps": [{"action": "scale", "expected_result": "latency recovers"}],
            "verification": ["p95 normal"],
            "rollback": ["restore replica count"],
            "owner_role": "SRE",
        },
    }, CATALOG)
    assert packet is not None
    assert packet["remediation"]["evidence_refs"] == [1]
    assert packet["remediation"]["steps"][0]["action"] == "scale"


def test_agent_prompt_excludes_untrusted_history_from_citations() -> None:
    prompt = _build_agent_prompt(
        CATALOG,
        conclusion="Evidence is insufficient",
        confidence=0.2,
        facts=[],
        inferences=[],
        unknowns=["Need metrics"],
        remediation=_fallback_remediation("en"),
        experience={"matched": True, "experience_id": 7, "content": "old token=secret", "match_type": "semantic", "similarity": 0.8},
        output_language="en",
    )
    assert "Historical reference (unverified)" in prompt
    assert "Evidence excerpts" in prompt
    assert "Do not execute changes" in prompt
    assert "old token=secret" not in prompt


def test_agent_prompt_redacts_every_interpolated_field() -> None:
    prompt = _build_agent_prompt(
        [{"id": 1, "kind": "alert", "locator": "token=locator-secret", "excerpt": "token=excerpt-secret"}],
        conclusion="token=conclusion-secret",
        confidence=0.8,
        facts=[{"text": "token=fact-secret", "evidence_refs": [1]}],
        inferences=[{"text": "token=inference-secret", "evidence_refs": [1]}],
        unknowns=["token=unknown-secret"],
        remediation={
            "risk_level": "high", "summary": "token=summary-secret",
            "preconditions": ["token=precondition-secret"],
            "steps": [{"action": "token=action-secret", "expected_result": "token=expected-secret"}],
            "verification": ["token=verification-secret"], "rollback": ["token=rollback-secret"],
        },
        experience={"matched": False},
        output_language="en",
    )
    assert "secret" not in prompt
    assert "<REDACTED:credential_assignment>" in prompt


def test_fallback_remediation_is_explicitly_not_evidence_backed() -> None:
    fallback = _fallback_remediation("en")
    assert fallback["basis"] == "safety_fallback"
    assert fallback["evidence_refs"] == []
