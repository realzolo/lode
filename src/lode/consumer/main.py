"""Kafka consumer: validate alerts (v1.1), dedupe, and persist a task.

Reliability contract (production-grade):

* Routing is purely topic-based: the topic maps to an application via
  ``application_kafka``. Messages that fail validation go to the dead-letter
  topic; messages whose topic is not mapped to any application go to the
  unassigned topic.
* A message is **idempotent** at the Kafka level: ``ingestion_events`` has a
  unique ``(topic, partition, offset)`` triple, so a redelivered record can
  never create a second alert/analysis.
* An application-scoped dedupe key suppresses an *additional* alert for an
  incident that already has an active (pending/running) analysis, so the same
  error event is never analyzed twice. A partial unique index on
  ``analysis_jobs`` is the database-level backstop for concurrent consumers.
* The consumer **creates a queued job and commits the Kafka offset
  immediately** after the persist transaction succeeds. It does NOT execute the
  analysis. Execution is the worker's job (``lode.worker``), which means a
  burst of alerts is absorbed by the queue and a crashed process loses nothing.
* The offset is committed only when ``process_message`` returns normally. A
  transient failure (DB/Kafka/DLQ unavailable) raises so the offset is NOT
  committed and the record is redelivered after a reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.config import settings
from lode.consumer.alert_schema import AlertMessage
from lode.consumer.dedupe import compute_dedupe_key
from lode.db.models.alert import Alert
from lode.db.models.analysis import Analysis, DeadLetter
from lode.db.models.application import ApplicationKafka
from lode.db.models.intake import AnalysisJob, Incident, IngestionEvent
from lode.db.session import AsyncSessionLocal
from lode.metrics import (
    ANALYSES,
    DEAD_LETTERS,
    ENGINE_IN_FLIGHT,
    MESSAGES_RECEIVED,
)

logger = logging.getLogger("lode.consumer")

ERROR_MAX_LENGTH = 500

# Raised for transient failures where the Kafka offset must NOT be committed
# (the record will be redelivered after the consumer reconnects).
class IngestionTransientError(Exception):
    pass


async def _produce(producer: AIOKafkaProducer, topic: str, value: dict[str, Any]) -> None:
    await producer.send_and_wait(
        topic, json.dumps(value, ensure_ascii=False).encode("utf-8")
    )


async def _record_dead_letter(
    db: AsyncSession | None,
    kind: str,
    topic: str,
    reason: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    application_id: int | None = None,
    partition: int | None = None,
    offset: int | None = None,
) -> None:
    """Persist a failed-intake message so operators can audit/replay it.

    A failure here raises ``IngestionTransientError`` so the caller does NOT
    commit the offset: a dead letter that cannot be recorded must not be
    silently swallowed.
    """
    DEAD_LETTERS.labels(kind=kind).inc()
    dl = DeadLetter(
        kind=kind,
        topic=topic,
        reason=reason,
        payload=payload,
        dedupe_key=dedupe_key,
        application_id=application_id,
        partition=partition,
        offset=offset,
    )
    if db is not None:
        db.add(dl)
        await db.flush()
        return
    async with AsyncSessionLocal() as s:
        s.add(dl)
        await s.commit()


async def resolve_application_id(session: AsyncSession, topic: str) -> int | None:
    result = await session.execute(
        select(ApplicationKafka.application_id).where(ApplicationKafka.topic == topic)
    )
    return result.scalar_one_or_none()


async def _dedupe_active_exists(
    session: AsyncSession, application_id: int, dedupe_key: str
) -> bool:
    """True if the incident already has an active (pending/running) analysis."""
    result = await session.execute(
        select(Analysis.id)
        .where(Analysis.application_id == application_id)
        .where(Analysis.dedupe_key == dedupe_key)
        .where(Analysis.status.in_(["pending", "running"]))
        .limit(1)
    )
    return result.scalars().first() is not None


async def _persist(
    db: AsyncSession,
    *,
    application_id: int,
    topic: str,
    partition: int | None,
    offset: int | None,
    dedupe_key: str,
    alert: Alert,
    producer_event_id: str | None,
    payload_hash: str | None,
    trace_id: str | None,
) -> tuple[int, int]:
    """Atomically record the ingestion event, alert, analysis and queued job.

    Returns ``(analysis_id, job_id)``. Raises ``IntegrityError`` (caught by the
    caller and treated as a duplicate) if a redelivery or a concurrent consumer
    beat us to the unique ``ingestion_events`` / active ``analysis_jobs`` row.
    """
    now = datetime.now(timezone.utc)
    ie = IngestionEvent(
        application_id=application_id,
        topic=topic,
        partition=partition,
        offset=offset,
        producer_event_id=producer_event_id,
        payload_hash=payload_hash,
        status="accepted",
        trace_id=trace_id,
    )
    db.add(ie)
    await db.flush()  # enforces (topic, partition, offset) uniqueness

    # Collapse into the incident for this application-scoped dedupe key.
    incident = (
        await db.execute(
            select(Incident)
            .where(Incident.application_id == application_id)
            .where(Incident.dedupe_key == dedupe_key)
        )
    ).scalars().first()
    if incident is None:
        incident = Incident(
            public_id=str(uuid.uuid4()),
            application_id=application_id,
            dedupe_key=dedupe_key,
            state="open",
            first_alert_id=None,
            latest_alert_id=None,
            alert_count=0,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(incident)
        await db.flush()
    else:
        incident.latest_alert_id = None
        incident.last_seen_at = now

    alert.incident_id = incident.id
    db.add(alert)
    await db.flush()

    analysis = Analysis(
        dedupe_key=dedupe_key,
        application_id=application_id,
        alert_id=alert.id,
        incident_id=incident.id,
        status="pending",
        engine_version=None,
    )
    db.add(analysis)
    await db.flush()

    job = AnalysisJob(
        public_id=str(uuid.uuid4()),
        incident_id=incident.id,
        analysis_id=analysis.id,
        trigger="ingest",
        status="queued",
        priority=0,
        attempt=0,
        max_attempts=settings.job_max_attempts,
        available_at=now,
        trace_id=trace_id,
    )
    db.add(job)
    await db.commit()
    incident.alert_count = (incident.alert_count or 0) + 1
    incident.latest_alert_id = alert.id
    await db.commit()
    return analysis.id, job.id


async def _route_failure(
    producer: AIOKafkaProducer,
    db: AsyncSession | None,
    *,
    topic: str,
    kind: str,
    reason: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    application_id: int | None = None,
    partition: int | None = None,
    offset: int | None = None,
) -> None:
    """Send to the DLQ topic and persist a dead letter, or raise on failure."""
    await _produce(
        producer,
        settings.kafka_dlq_topic if kind == "dlq" else settings.kafka_unassigned_topic,
        {"topic": topic, "dedupe_key": dedupe_key, "raw": payload} if kind == "unassigned"
        else {"topic": topic, "error": reason, "raw": payload},
    )
    await _record_dead_letter(
        db, kind, topic, reason, payload,
        dedupe_key=dedupe_key, application_id=application_id,
        partition=partition, offset=offset,
    )


async def process_message(
    topic: str,
    raw: bytes,
    producer: AIOKafkaProducer,
    partition: int | None = None,
    offset: int | None = None,
    session: AsyncSession | None = None,
    trace_id: str | None = None,
) -> str:
    """Validate, route, dedupe, persist a queued job, and return a status.

    ``session`` is injectable so the function can be exercised without a live
    broker or database. When ``session`` is omitted a real ``AsyncSessionLocal``
    is used.

    Returns one of ``persisted | duplicate | dlq | unassigned``. A transient
    failure raises ``IngestionTransientError`` so the caller skips the offset
    commit and the record is redelivered.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        MESSAGES_RECEIVED.labels(outcome="dlq").inc()
        await _route_failure(
            producer, session, topic=topic, kind="dlq",
            reason=f"invalid json: {exc}",
            payload={"raw": raw.decode("utf-8", "replace")},
            partition=partition, offset=offset,
        )
        return "dlq"

    try:
        msg = AlertMessage(**data)
    except ValidationError as exc:
        MESSAGES_RECEIVED.labels(outcome="dlq").inc()
        await _route_failure(
            producer, session, topic=topic, kind="dlq",
            reason=f"schema validation failed: {exc}", payload=data,
            partition=partition, offset=offset,
        )
        return "dlq"

    dedupe_key = compute_dedupe_key(
        event_type=msg.event_type, title=msg.title, fields=msg.fields
    )

    if session is not None:
        return await _process_with_session(
            session, producer, topic, dedupe_key, msg, data, partition, offset, trace_id
        )

    async with AsyncSessionLocal() as db:
        return await _process_with_session(
            db, producer, topic, dedupe_key, msg, data, partition, offset, trace_id
        )


async def _process_with_session(
    session: AsyncSession,
    producer: AIOKafkaProducer,
    topic: str,
    dedupe_key: str,
    msg: AlertMessage,
    data: dict[str, Any],
    partition: int | None,
    offset: int | None,
    trace_id: str | None,
) -> str:
    app_id = await resolve_application_id(session, topic)
    if app_id is None:
        MESSAGES_RECEIVED.labels(outcome="unassigned").inc()
        await _route_failure(
            producer, session, topic=topic, kind="unassigned",
            reason="topic not mapped to any application",
            payload={"topic": topic, "dedupe_key": dedupe_key, "raw": data},
            dedupe_key=dedupe_key, partition=partition, offset=offset,
        )
        return "unassigned"

    error_value = msg.fields.get("error")
    error_message = (
        str(error_value)[:ERROR_MAX_LENGTH] if error_value not in (None, "") else ""
    )
    alert = Alert(
        dedupe_key=dedupe_key,
        application_id=app_id,
        topic=topic,
        title=msg.title,
        level=msg.level_value,
        env=msg.env,
        error_message=error_message,
        fields=msg.fields,
        raw_payload=msg.model_dump(),
    )

    # Suppress an additional alert for an incident with an active analysis.
    if await _dedupe_active_exists(session, app_id, dedupe_key):
        MESSAGES_RECEIVED.labels(outcome="duplicate").inc()
        logger.info(
            "skipping duplicate active analysis for application_id=%s dedupe_key=%s",
            app_id, dedupe_key,
        )
        return "duplicate"

    try:
        analysis_id, _job_id = await _persist(
            session,
            application_id=app_id,
            topic=topic,
            partition=partition,
            offset=offset,
            dedupe_key=dedupe_key,
            alert=alert,
            producer_event_id=None,
            payload_hash=None,
            trace_id=trace_id,
        )
    except IntegrityError:
        await session.rollback()
        return "duplicate"

    MESSAGES_RECEIVED.labels(outcome="persisted").inc()
    ANALYSES.labels(result="scheduled").inc()
    return "persisted"


async def main() -> None:
    """Run the consumer, reconnecting transparently if Kafka is unavailable.

    The loop tolerates a broker that is slow to come up (common in
    docker-compose). A transient per-message failure breaks the inner loop so
    the offset is NOT committed; the outer loop recreates the consumer and
    redelivers the failed record from the last committed offset.
    """
    while True:
        try:
            consumer = AIOKafkaConsumer(
                settings.kafka_topic_pattern,
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=settings.kafka_group_id,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
            )
            producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
            await consumer.start()
            await producer.start()
            logger.info("consumer started on %s", settings.kafka_bootstrap_servers)
            try:
                async for record in consumer:
                    try:
                        await process_message(
                            record.topic, record.value, producer,
                            record.partition, record.offset,
                        )
                    except IngestionTransientError:
                        logger.exception(
                            "transient intake failure on topic %s; pausing redelivery",
                            record.topic,
                        )
                        raise
                    await consumer.commit()
            finally:
                await consumer.stop()
                await producer.stop()
            return
        except KafkaConnectionError as exc:
            logger.warning("kafka unavailable, retrying in 5s: %s", exc)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except IngestionTransientError:
            logger.warning("transient intake error, reconnecting to redeliver")
            await asyncio.sleep(2)
        except Exception as exc:
            logger.exception("consumer error, retrying in 5s: %s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
