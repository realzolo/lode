"""Append-only execution facts for the canonical investigation pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text

from lode.db.models.investigation import EXECUTION_EVENT_PHASES, InvestigationExecutionEvent
from lode.engine.evidence.secret_mask import mask_secrets


def _safe_detail(value: dict[str, Any] | None) -> dict[str, Any]:
    """Keep event payloads operationally useful without leaking credentials."""
    safe: dict[str, Any] = {}
    for key, raw in (value or {}).items():
        if isinstance(raw, str):
            safe[str(key)] = mask_secrets(raw)[0][:2_000]
        elif isinstance(raw, (int, float, bool)) or raw is None:
            safe[str(key)] = raw
        elif isinstance(raw, list):
            safe[str(key)] = [mask_secrets(str(item))[0][:500] for item in raw[:50]]
        elif isinstance(raw, dict):
            safe[str(key)] = _safe_detail({str(nested_key): nested for nested_key, nested in raw.items()})
        else:
            safe[str(key)] = mask_secrets(str(raw))[0][:2_000]
    return safe


async def append_execution_event(
    session,
    *,
    investigation_id: int,
    stage_id: int | None,
    node_id: int | None = None,
    event_type: str,
    phase: str,
    operation_id: str | None = None,
    collection_id: int | None = None,
    detail: dict[str, Any] | None = None,
    artifact_refs: list[int] | None = None,
    commit: bool = False,
) -> str:
    """Persist one fact. Callers append terminal records rather than updating it."""
    if phase not in EXECUTION_EVENT_PHASES:
        raise ValueError(f"unsupported investigation execution phase: {phase}")
    # Independent read-only collection waves use separate sessions.  Lock the
    # per-investigation sequence allocation so concurrent workers cannot emit
    # duplicate cursors for SSE replay.
    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": investigation_id})
    sequence = int((await session.execute(
        select(func.coalesce(func.max(InvestigationExecutionEvent.sequence), 0)).where(
            InvestigationExecutionEvent.investigation_id == investigation_id
        )
    )).scalar_one()) + 1
    event = InvestigationExecutionEvent(
        investigation_id=investigation_id,
        stage_id=stage_id,
        node_id=node_id,
        collection_id=collection_id,
        operation_id=operation_id or uuid.uuid4().hex,
        sequence=sequence,
        event_type=event_type,
        phase=phase,
        detail=_safe_detail(detail),
        artifact_refs=[item for item in (artifact_refs or []) if isinstance(item, int)],
        occurred_at=datetime.now(UTC),
    )
    session.add(event)
    await session.flush()
    if commit:
        await session.commit()
    return event.operation_id
