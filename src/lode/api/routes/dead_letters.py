"""Dead-letter audit & replay (DLQ / unmapped-topic messages).

These endpoints let operators see what the consumer rejected and re-inject a
message onto its source topic so the consumer re-processes it. Both are
admin-only: peeking at rejected payloads and replaying them are sensitive
operations.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiokafka import AIOKafkaProducer

from lode.api.audit import audit_action
from lode.api.deps import require_admin
from lode.api.schemas import DeadLetterOut, ReplayOut
from lode.config import settings
from lode.db.models.analysis import DeadLetter
from lode.db.session import AsyncSessionLocal

logger = logging.getLogger("lode.api.dead_letters")

router = APIRouter(prefix="/dead-letters", tags=["dead-letters"])


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@router.get("", response_model=list[DeadLetterOut])
async def list_dead_letters(
    kind: str | None = Query(default=None, pattern="^(dlq|unassigned)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[DeadLetterOut]:
    stmt = select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(limit)
    if kind:
        stmt = stmt.where(DeadLetter.kind == kind)
    rows = (await session.execute(stmt)).scalars().all()
    return [DeadLetterOut.model_validate(r) for r in rows]


@router.post("/{dead_letter_id}/replay", response_model=ReplayOut)
async def replay_dead_letter(
    dead_letter_id: int,
    _admin: int = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ReplayOut:
    dl = await session.get(DeadLetter, dead_letter_id)
    if dl is None:
        raise HTTPException(status_code=404, detail="dead letter not found")

    # Re-inject the original payload onto its source topic. The consumer wraps
    # some payloads in a {"raw": ...} envelope, so unwrap that before sending.
    payload = dl.payload
    if isinstance(payload, dict) and "raw" in payload:
        payload = payload["raw"]
    if isinstance(payload, str):
        body = payload.encode("utf-8")
    elif isinstance(payload, (dict, list)):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    else:
        body = str(payload).encode("utf-8")

    # Best-effort: a Kafka outage returns 502 instead of silently dropping.
    try:
        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        await producer.start()
        try:
            await producer.send_and_wait(dl.topic, body)
        finally:
            await producer.stop()
    except Exception as exc:  # noqa: BLE001 - surface infra errors to the operator
        logger.exception("replay producer failed for dead letter %s", dead_letter_id)
        await audit_action(
            session,
            action="dlq.replay",
            actor_id=_admin,
            target_type="dead_letter",
            target_id=str(dead_letter_id),
            application_id=dl.application_id,
            result="error",
            detail={"error": str(exc)},
        )
        raise HTTPException(status_code=502, detail=f"kafka producer failed: {exc}")

    dl.replayed = True
    await session.commit()
    await audit_action(
        session,
        action="dlq.replay",
        actor_id=_admin,
        target_type="dead_letter",
        target_id=str(dead_letter_id),
        application_id=dl.application_id,
        result="ok",
    )
    return ReplayOut(id=dl.id, topic=dl.topic, status="replayed")
