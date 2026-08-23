"""Kafka consumer: validate alerts (v1.1), recompute dedupe key, persist.

Routing is purely topic-based: the topic maps to an application via
``application_kafka``. Messages that fail validation go to the dead-letter
topic; messages whose topic is not mapped to any application go to the
unassigned topic. Both are still committed so the consumer makes progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from pydantic import ValidationError
from sqlalchemy import select

from lode.config import settings
from lode.consumer.alert_schema import AlertMessage
from lode.consumer.dedupe import compute_dedupe_key
from lode.db.models.alert import Alert
from lode.db.models.analysis import Analysis, DeadLetter
from lode.db.models.application import ApplicationKafka
from lode.db.session import AsyncSessionLocal
from lode.engine import run_analysis
from lode.metrics import (
    ANALYSES,
    DEAD_LETTERS,
    ENGINE_IN_FLIGHT,
    MESSAGES_RECEIVED,
)

logger = logging.getLogger("lode.consumer")

ERROR_MAX_LENGTH = 500

# Default scheduler: run the analysis engine off the consumer's event loop so
# ingestion keeps moving. Tests inject a no-op to keep the consumer hermetic.
_DEFAULT_SCHEDULE: Callable[[int], Awaitable[Any]] = lambda analysis_id: asyncio.create_task(
    _run_analysis_in_background(analysis_id)
)

# Bound concurrent analyses so a burst of redeliveries cannot exhaust the DB
# connection pool or trip the LLM provider's rate limit. Created at import time;
# in Python 3.10+ a Semaphore is not bound to a specific event loop.
_ENGINE_SEMAPHORE = asyncio.Semaphore(settings.engine_concurrency)


async def _produce(producer: AIOKafkaProducer, topic: str, value: dict[str, Any]) -> None:
    await producer.send_and_wait(
        topic, json.dumps(value, ensure_ascii=False).encode("utf-8")
    )


async def _record_dead_letter(
    db: Any,
    kind: str,
    topic: str,
    reason: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
) -> None:
    """Persist a failed-intake message so operators can audit/replay it.

    Reuses the caller's session when one is injected (keeps hermetic tests off
    the real database); otherwise writes through a fresh session. A failure here
    must never block the ingest stream, so it is swallowed and logged.
    """
    dl = DeadLetter(
        kind=kind, topic=topic, reason=reason, payload=payload, dedupe_key=dedupe_key
    )
    DEAD_LETTERS.labels(kind=kind).inc()
    if db is not None:
        db.add(dl)
        await db.flush()
        return
    try:
        async with AsyncSessionLocal() as s:
            s.add(dl)
            await s.commit()
    except Exception:
        logger.exception("failed to record dead letter (%s)", kind)


async def resolve_application_id(session, topic: str) -> int | None:
    result = await session.execute(
        select(ApplicationKafka.application_id).where(ApplicationKafka.topic == topic)
    )
    return result.scalar_one_or_none()


async def process_message(
    topic: str,
    raw: bytes,
    producer: AIOKafkaProducer,
    session: Any = None,
    schedule: Callable[[int], Awaitable[Any]] | None = None,
) -> None:
    """Validate, route, and persist one alert message.

    ``session`` and ``schedule`` are injectable so the function can be exercised
    without a live broker or database. When ``session`` is omitted a real
    ``AsyncSessionLocal`` is used; when ``schedule`` is omitted the analysis is
    handed to the engine via the event loop.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        MESSAGES_RECEIVED.labels(outcome="dlq").inc()
        await _produce(
            producer,
            settings.kafka_dlq_topic,
            {"topic": topic, "error": f"invalid json: {exc}", "raw": raw.decode("utf-8", "replace")},
        )
        await _record_dead_letter(
            session,
            "dlq",
            topic,
            f"invalid json: {exc}",
            {"raw": raw.decode("utf-8", "replace")},
        )
        return

    try:
        msg = AlertMessage(**data)
    except ValidationError as exc:
        # schema_version outside the supported 1.x line (e.g. a 2.x breaking
        # change) lands here and is dead-lettered rather than silently dropped.
        MESSAGES_RECEIVED.labels(outcome="dlq").inc()
        await _produce(
            producer,
            settings.kafka_dlq_topic,
            {"topic": topic, "error": f"schema validation failed: {exc}", "raw": data},
        )
        await _record_dead_letter(
            session, "dlq", topic, f"schema validation failed: {exc}", data
        )
        return

    # Forward-compatible but worth knowing: the producer shipped a schema newer
    # than what this consumer was built against. Processing continues (1.x is
    # forward-tolerant) but operators get a heads-up to refresh the consumer.
    if getattr(msg, "schema_version", None) != "1.1":
        logger.warning(
            "alert on topic %s uses schema_version=%s (consumer understands 1.1); "
            "processing with backward-compatible handling",
            topic, msg.schema_version,
        )

    dedupe_key = compute_dedupe_key(
        event_type=msg.event_type, title=msg.title, fields=msg.fields
    )

    # Idempotency: Kafka delivery is at-least-once, so the same incident can be
    # redelivered. If an in-flight (pending/running) analysis already exists for
    # this dedupe_key, reuse it instead of spawning a duplicate — re-running it
    # would waste LLM calls and double-count. A completed/failed analysis is NOT
    # suppressed here; that is deliberate re-analysis the user may trigger.
    async def _lookup_in_flight() -> int | None:
        stmt = (
            select(Analysis.id)
            .where(Analysis.dedupe_key == dedupe_key)
            .where(Analysis.status.in_(["pending", "running"]))
            .order_by(Analysis.id.desc())
        )
        if session is not None:
            res = await session.execute(stmt)
        else:
            async with AsyncSessionLocal() as db:
                res = await db.execute(stmt)
        return res.scalars().first()

    in_flight_id = await _lookup_in_flight()
    if in_flight_id is not None:
        logger.info("skipping duplicate in-flight analysis for dedupe_key=%s", dedupe_key)
        MESSAGES_RECEIVED.labels(outcome="duplicate").inc()
        return

    async def _persist(db: Any) -> int | None:
        application_id = await resolve_application_id(db, topic)
        if application_id is None:
            MESSAGES_RECEIVED.labels(outcome="unassigned").inc()
            await _produce(
                producer,
                settings.kafka_unassigned_topic,
                {"topic": topic, "dedupe_key": dedupe_key, "raw": data},
            )
            await _record_dead_letter(
                session,
                "unassigned",
                topic,
                "topic not mapped to any application",
                {"topic": topic, "dedupe_key": dedupe_key, "raw": data},
                dedupe_key=dedupe_key,
            )
            return None

        error_value = msg.fields.get("error")
        error_message = (
            str(error_value)[:ERROR_MAX_LENGTH] if error_value not in (None, "") else ""
        )

        alert = Alert(
            dedupe_key=dedupe_key,
            application_id=application_id,
            topic=topic,
            title=msg.title,
            level=msg.level_value,
            env=msg.env,
            error_message=error_message,
            fields=msg.fields,
            raw_payload=data,
        )
        db.add(alert)
        await db.flush()

        analysis = Analysis(
            dedupe_key=dedupe_key,
            application_id=application_id,
            alert_id=alert.id,
            status="pending",
        )
        db.add(analysis)
        await db.commit()
        logger.info(
            "persisted alert dedupe_key=%s application_id=%s", dedupe_key, application_id
        )
        return analysis.id

    if session is not None:
        analysis_id = await _persist(session)
    else:
        async with AsyncSessionLocal() as db:
            analysis_id = await _persist(db)

    if analysis_id is not None:
        MESSAGES_RECEIVED.labels(outcome="persisted").inc()
        ANALYSES.labels(result="scheduled").inc()
        (schedule or _DEFAULT_SCHEDULE)(analysis_id)


async def _run_analysis_in_background(analysis_id: int) -> None:
    """Drive the engine to completion; never crash the consumer on failure."""
    ENGINE_IN_FLIGHT.inc()
    try:
        async with _ENGINE_SEMAPHORE:
            try:
                async with AsyncSessionLocal() as session:
                    await run_analysis(analysis_id, session)
            except Exception as exc:
                logger.exception("engine failed for analysis %s: %s", analysis_id, exc)
                ANALYSES.labels(result="failed").inc()
                try:
                    async with AsyncSessionLocal() as session:
                        result = await session.execute(
                            select(Analysis).where(Analysis.id == analysis_id)
                        )
                        analysis = result.scalars().first()
                        if analysis is not None and analysis.status != "completed":
                            analysis.status = "failed"
                            await session.commit()
                except Exception:
                    logger.exception("could not mark analysis %s failed", analysis_id)
    finally:
        ENGINE_IN_FLIGHT.dec()


async def main() -> None:
    """Run the consumer, reconnecting transparently if Kafka is unavailable.

    The loop tolerates a broker that is slow to come up (common in
    docker-compose) and survives transient per-message failures by logging and
    committing the offset so ingestion always makes forward progress.
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
                        await process_message(record.topic, record.value, producer)
                    except Exception:
                        logger.exception(
                            "failed to process message on topic %s", record.topic
                        )
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
        except Exception as exc:
            logger.exception("consumer error, retrying in 5s: %s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
