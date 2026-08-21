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
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import ValidationError
from sqlalchemy import select

from incident_trace.config import settings
from incident_trace.consumer.alert_schema import AlertMessage
from incident_trace.consumer.dedupe import compute_dedupe_key
from incident_trace.db.models.alert import Alert
from incident_trace.db.models.analysis import Analysis
from incident_trace.db.models.application import ApplicationKafka
from incident_trace.db.session import AsyncSessionLocal
from incident_trace.engine import run_analysis

logger = logging.getLogger("incident_trace.consumer")

ERROR_MAX_LENGTH = 500


async def _produce(producer: AIOKafkaProducer, topic: str, value: dict[str, Any]) -> None:
    await producer.send_and_wait(
        topic, json.dumps(value, ensure_ascii=False).encode("utf-8")
    )


async def resolve_application_id(session, topic: str) -> int | None:
    result = await session.execute(
        select(ApplicationKafka.application_id).where(ApplicationKafka.topic == topic)
    )
    return result.scalar_one_or_none()


async def process_message(
    topic: str, raw: bytes, producer: AIOKafkaProducer
) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        await _produce(
            producer,
            settings.kafka_dlq_topic,
            {"topic": topic, "error": f"invalid json: {exc}", "raw": raw.decode("utf-8", "replace")},
        )
        return

    try:
        msg = AlertMessage(**data)
    except ValidationError as exc:
        await _produce(
            producer,
            settings.kafka_dlq_topic,
            {"topic": topic, "error": f"schema validation failed: {exc}", "raw": data},
        )
        return

    dedupe_key = compute_dedupe_key(
        event_type=msg.event_type, title=msg.title, fields=msg.fields
    )

    async with AsyncSessionLocal() as session:
        application_id = await resolve_application_id(session, topic)
        if application_id is None:
            await _produce(
                producer,
                settings.kafka_unassigned_topic,
                {"topic": topic, "dedupe_key": dedupe_key, "raw": data},
            )
            return

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
        session.add(alert)
        await session.flush()

        analysis = Analysis(
            dedupe_key=dedupe_key,
            application_id=application_id,
            alert_id=alert.id,
            status="pending",
        )
        session.add(analysis)
        await session.commit()
        # Hand the analysis off to the engine in a background task so the
        # consumer keeps ingesting. A fresh session is used in the task to
        # avoid sharing the request's connection across loops/threads.
        analysis_id = analysis.id

    logger.info("persisted alert dedupe_key=%s application_id=%s", dedupe_key, application_id)
    asyncio.create_task(_run_analysis_in_background(analysis_id))


async def _run_analysis_in_background(analysis_id: int) -> None:
    """Drive the engine to completion; never crash the consumer on failure."""
    try:
        async with AsyncSessionLocal() as session:
            await run_analysis(analysis_id, session)
    except Exception as exc:  # noqa: BLE001 - mark failed, keep consumer alive
        logger.exception("engine failed for analysis %s: %s", analysis_id, exc)
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Analysis).where(Analysis.id == analysis_id)
                )
                analysis = result.scalars().first()
                if analysis is not None and analysis.status != "completed":
                    analysis.status = "failed"
                    await session.commit()
        except Exception:  # noqa: BLE001 - best-effort only
            logger.exception("could not mark analysis %s failed", analysis_id)


async def main() -> None:
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
            await process_message(record.topic, record.value, producer)
            await consumer.commit()
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
