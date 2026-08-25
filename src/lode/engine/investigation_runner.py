"""Compatibility helpers and the dynamic investigation entry point."""

from __future__ import annotations

import json
import re
from typing import Any

from lode.engine.evidence.secret_mask import mask_secrets


def _zh(value: str) -> bool:
    return len(re.findall(r"[\u4e00-\u9fff]", value)) >= 2


def _safe(value: object, limit: int = 2_000) -> str:
    return mask_secrets(str(value or "").strip())[0][:limit]


def _fallback(language: str) -> tuple[str, list[str], dict[str, Any]]:
    """Deterministic authoritative failure boundary for legacy callers."""
    if language == "zh":
        return (
            "调查结论：现有证据确认了告警现象；在当前调查范围内，故障边界收敛于该已验证结果。",
            ["补充事故时间窗内的脱敏证据：日志、trace、部署版本或上游响应，以区分当前假设。"],
            {
                "summary": "先补充最有区分力的证据；在证据验证前不执行生产变更。",
                "risk_level": "low",
                "preconditions": ["确认补充证据与事故时间窗相关。"],
                "steps": [{"action": "提交最小缺失证据并自动重新调查。", "expected_result": "生成继承证据的精确结论。"}],
                "verification": ["确认新证据的时间范围和请求关联。"],
                "rollback": ["不执行生产变更，因此无需回滚。"],
            },
        )
    return (
        "Investigation conclusion: current evidence confirms the alert symptom and the failure boundary is the verified result within this investigation scope.",
        ["Provide a redacted incident-window log, trace, deployed revision, or upstream response to distinguish the current hypotheses."],
        {
            "summary": "Collect the most discriminating evidence before any production change.",
            "risk_level": "low",
            "preconditions": ["Confirm additional evidence correlates to the incident window."],
            "steps": [{"action": "Submit the minimum missing evidence and automatically reinvestigate.", "expected_result": "Produce a more precise conclusion with inherited evidence."}],
            "verification": ["Confirm the new evidence time range and request correlation."],
            "rollback": ["No production change is executed, so no rollback is required."],
        },
    )


def _parse_packet(text: str | None, artifacts: list[Any], language: str) -> dict[str, Any] | None:
    """Validate an older structured packet without accepting fabricated refs."""
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    valid_ids = {artifact.id for artifact in artifacts}
    conclusion = value.get("conclusion")
    if isinstance(conclusion, dict):
        conclusion = conclusion.get("text")
    if not isinstance(conclusion, str) or not conclusion.strip():
        return None
    conclusion_value = value.get("conclusion")
    refs = value.get("evidence_refs") or (conclusion_value.get("evidence_refs", []) if isinstance(conclusion_value, dict) else [])
    refs = [item for item in refs if isinstance(item, int) and item in valid_ids]
    if not refs:
        return None
    human = [conclusion]
    for key in ("facts", "inferences", "unknowns"):
        raw = value.get(key) or []
        if isinstance(raw, list):
            human.extend(item.get("text", "") if isinstance(item, dict) else item for item in raw)
    remediation = value.get("remediation") if isinstance(value.get("remediation"), dict) else {}
    human.append(str(remediation.get("summary", "")))
    if language == "zh" and not all(not isinstance(item, str) or not item.strip() or _zh(item) for item in human):
        return None
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence", 0.4))))
    except (TypeError, ValueError):
        confidence = 0.4

    def safe_items(items: object) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        return [{**item, "text": _safe(item["text"], 1_000)} for item in items if isinstance(item, dict) and isinstance(item.get("text"), str)]

    return {
        "conclusion": _safe(conclusion),
        "confidence": confidence,
        "refs": refs,
        "facts": safe_items(value.get("facts")),
        "inferences": safe_items(value.get("inferences")),
        "unknowns": [_safe(item, 500) for item in value.get("unknowns", []) if isinstance(item, str)][:12],
        "remediation": remediation,
    }


async def run_investigation(investigation_id: int, session) -> None:
    """Execute the capability-driven graph; fixed stage pipelines are retired."""
    from lode.engine.investigation_graph import run_dynamic_investigation

    await run_dynamic_investigation(investigation_id, session)
