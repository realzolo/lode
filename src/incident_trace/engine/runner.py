"""Phase 1 analysis runner.

Drives the agentic workflow for a single analysis:

    receive -> git_sync -> context -> ai_analysis -> memory -> conclusion

Each node is persisted as an ``analysis_steps`` row so the UI can render the
exact path the agent took. The agent may call four controlled, read-only
tools (see ``tools.py``). An LLM is used when a model is configured; otherwise
a deterministic heuristic produces a coherent conclusion so the product stays
runnable without external API keys.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from incident_trace.db.models.ai_model import AiModelConfig
from incident_trace.db.models.analysis import Analysis, AnalysisStep, AnalysisHint
from incident_trace.db.models.memory import Memory
from incident_trace.engine.llm import ModelConfig, complete
from incident_trace.engine.tools import (
    get_deploy_context,
    get_memory,
    load_alert,
    run_readonly_query,
    search_code,
)

logger = logging.getLogger("incident_trace.engine.runner")

_NODE_ORDER = ["receive", "git_sync", "context", "ai_analysis", "memory", "conclusion"]


def _now() -> datetime:
    return datetime.now()


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from the model output."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTASPACE | re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _heuristic_conclusion(
    alert, deploy_prompt: str | None, memory_content: str | None, fields: dict
) -> tuple[str, float]:
    """Deterministic offline fallback used when no LLM is configured."""
    env = getattr(alert, "env", "") or "production"
    error = getattr(alert, "error_message", "") or "no error message captured"
    title = getattr(alert, "title", "") or "incident"

    parts = [
        f"Incident \"{title}\" in {env}.",
        f"Captured error: {error}.",
    ]
    if deploy_prompt:
        parts.append(f"Deploy context: {deploy_prompt}")
    if memory_content:
        parts.append(f"A matching prior incident is recorded in shared memory: {memory_content}")

    conclusion = " ".join(parts)
    confidence = 0.55
    if deploy_prompt:
        confidence += 0.15
    if memory_content:
        confidence += 0.15
    if error and error != "no error message captured":
        confidence += 0.10
    confidence = min(0.95, confidence)
    return conclusion, round(confidence, 2)


async def _resolve_model_config(session, application_id: int) -> ModelConfig | None:
    result = await session.execute(
        select(AiModelConfig)
        .where(AiModelConfig.scope == "application")
        .where(AiModelConfig.application_id == application_id)
        .where(AiModelConfig.is_default.is_(True))
    )
    cfg = result.scalars().first()
    if cfg is None:
        result = await session.execute(
            select(AiModelConfig)
            .where(AiModelConfig.scope == "global")
            .where(AiModelConfig.is_default.is_(True))
        )
        cfg = result.scalars().first()
    if cfg is None:
        return None
    return ModelConfig(
        provider=cfg.provider,
        base_url=cfg.base_url,
        api_key_ref=cfg.api_key_ref,
        model=cfg.model,
    )


def _build_prompts(alert, deploy_prompt, modules, allowed_tables, memory_content) -> tuple[str, str]:
    system = (
        "You are a senior SRE performing root-cause analysis for a production "
        "incident. Use ONLY the provided context. Respond with a single JSON "
        "object: {\"conclusion\": string, \"confidence\": number between 0 and 1, "
        "\"evidence\": {string: string}}. Be concise and specific."
    )
    lines = [
        f"Title: {getattr(alert, 'title', '')}",
        f"Level: {getattr(alert, 'level', '')}",
        f"Environment: {getattr(alert, 'env', '')}",
        f"Error: {getattr(alert, 'error_message', '')}",
        f"Fields: {json.dumps(getattr(alert, 'fields', {}), ensure_ascii=False)}",
    ]
    if deploy_prompt:
        lines.append(f"Deploy context: {deploy_prompt}")
    if modules:
        lines.append(f"Modules available: {', '.join(modules)}")
    if allowed_tables:
        lines.append(f"Read-only tables: {', '.join(allowed_tables)}")
    if memory_content:
        lines.append(f"Matched shared memory: {memory_content}")
    lines.append(
        "Return JSON {\"conclusion\", \"confidence\", \"evidence\"} and nothing else."
    )
    return system, "\n".join(lines)


async def run_analysis(analysis_id: int, session) -> None:
    """Execute the full workflow for ``analysis_id`` and persist results."""
    result = await session.execute(select(Analysis).where(Analysis.id == analysis_id))
    analysis = result.scalars().first()
    if analysis is None:
        logger.warning("run_analysis: analysis %s not found", analysis_id)
        return

    analysis.status = "running"
    analysis.started_at = _now()
    # Clear any prior workflow nodes so re-analysis starts clean.
    existing = await session.execute(
        select(AnalysisStep).where(AnalysisStep.analysis_id == analysis_id)
    )
    for step in existing.scalars().all():
        await session.delete(step)
    await session.flush()

    def _step(node_type: str, status: str, summary: str, detail: str) -> None:
        session.add(
            AnalysisStep(
                analysis_id=analysis_id,
                node_type=node_type,
                status=status,
                order_index=_NODE_ORDER.index(node_type),
                input={},
                output={"summary": summary, "detail": detail},
            )
        )

    _step("receive", "completed", "Alert received",
          f"Routed via topic {getattr(analysis, 'topic', '') or 'n/a'}")

    application_id = analysis.application_id
    alert = await load_alert(session, analysis.alert_id)

    code = await search_code(session, application_id)
    _step("git_sync", "completed", "Source modules loaded",
          "; ".join(code["modules_searched"]) or "no repositories registered")

    ctx = await get_deploy_context(session, application_id)
    _step("context", "completed", "Deploy context gathered",
          (ctx["deploy_prompt"] or "no deploy description configured")[:280])

    ro = await run_readonly_query(session, application_id)
    memory = await get_memory(session, application_id, analysis.dedupe_key)

    model_config = await _resolve_model_config(session, application_id)
    system, user = _build_prompts(
        alert,
        ctx["deploy_prompt"],
        code["modules_searched"],
        ro["allowed_tables"],
        memory["content"] if memory["matched"] else None,
    )
    llm_text = await complete(system, user, model_config)

    if llm_text:
        parsed = _parse_llm_json(llm_text)
        if parsed and isinstance(parsed, dict) and parsed.get("conclusion"):
            conclusion = str(parsed["conclusion"])
            try:
                confidence = float(parsed.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            confidence = max(0.0, min(1.0, confidence))
            evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
            engine_used = "llm"
        else:
            conclusion, confidence = _heuristic_conclusion(
                alert, ctx["deploy_prompt"], memory["content"] if memory["matched"] else None,
                getattr(alert, "fields", {}) or {},
            )
            evidence = {}
            engine_used = "heuristic"
    else:
        conclusion, confidence = _heuristic_conclusion(
            alert, ctx["deploy_prompt"], memory["content"] if memory["matched"] else None,
            getattr(alert, "fields", {}) or {},
        )
        evidence = {}
        engine_used = "heuristic"

    _step("ai_analysis", "completed", f"Root cause synthesized ({engine_used})",
          conclusion[:280])

    if memory["matched"]:
        _step("memory", "completed", "Matched shared memory",
              memory["content"][:280])
    else:
        _step("memory", "completed", "No prior memory",
              "No matching shared memory; a new entry will be recorded.")

    # Grow shared memory when we are confident and had no prior match.
    # Upsert by trigger_signature so repeated re-analyses never create
    # duplicate memory rows.
    if not memory["matched"] and confidence >= 0.7 and conclusion:
        prior = await session.execute(
            select(Memory)
            .where(Memory.application_id == application_id)
            .where(Memory.trigger_signature == analysis.dedupe_key)
        )
        existing = prior.scalars().first()
        if existing is None:
            session.add(
                Memory(
                    application_id=application_id,
                    trigger_signature=analysis.dedupe_key,
                    content=conclusion,
                    source_analysis_id=analysis_id,
                    is_valid=True,
                )
            )
        else:
            existing.content = conclusion
            existing.is_valid = True
            existing.source_analysis_id = analysis_id

    evidence = {
        "engine": engine_used,
        "env": getattr(alert, "env", ""),
        "error_message": getattr(alert, "error_message", ""),
        "modules": code["modules_searched"],
        "allowed_tables": ro["allowed_tables"],
        "matched_memory": bool(memory["matched"]),
        **evidence,
    }

    _step("conclusion", "completed", "Conclusion ready",
          f"Confidence {confidence:.2f}")

    analysis.conclusion = conclusion
    analysis.confidence = confidence
    analysis.evidence = evidence
    analysis.status = "completed"
    analysis.finished_at = _now()
    await session.commit()
    logger.info("analysis %s completed (engine=%s, confidence=%.2f)",
                analysis_id, engine_used, confidence)
