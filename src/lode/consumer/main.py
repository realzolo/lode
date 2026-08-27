"""Strict Kafka `incident.alert.v1` transport adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Protocol

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.structs import OffsetAndMetadata
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.application.intake import KafkaIncidentAlert, mask_failure_payload, normalize_kafka
from lode.config import kafka_security_kwargs, settings
from lode.db.models import DeadLetter, IngestionEvent, Workspace
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.intake_store import IntakeResult, PostgresIntakeStore
from lode.metrics import ACTIVE_WORKSPACES, CONSUMER_HEARTBEAT
from lode.runtime_defaults import (
    KAFKA_BATCH_MAX_RECORDS,
    KAFKA_CONSUMER_GROUP_ID,
    KAFKA_DEAD_LETTER_TOPIC,
    KAFKA_SUBSCRIPTION_REFRESH_SECONDS,
    KAFKA_UNASSIGNED_TOPIC,
)

logger = logging.getLogger("lode.consumer")


class FailurePublisher(Protocol):
    async def send_and_wait(
        self, topic: str, value: bytes, key: bytes | None = None
    ) -> Any: ...


class ConsumerRecord(Protocol):
    topic: str
    partition: int
    offset: int
    value: bytes


class OffsetCommitter(Protocol):
    async def commit(self, offsets: dict[TopicPartition, OffsetAndMetadata]) -> Any: ...


class RecordHandler(Protocol):
    async def handle(
        self, *, topic: str, partition: int, offset: int, raw: bytes
    ) -> IntakeResult: ...


def _validation_detail(error: ValidationError) -> dict[str, Any]:
    return {
        "errors": [
            {key: item[key] for key in ("loc", "msg", "type") if key in item}
            for item in error.errors()
        ]
    }


class KafkaIntakeHandler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        publisher: FailurePublisher | None = None,
    ):
        self._session_factory = session_factory
        self._publisher = publisher

    async def handle(
        self, *, topic: str, partition: int, offset: int, raw: bytes
    ) -> IntakeResult:
        payload_hash = hashlib.sha256(raw).hexdigest()
        async with self._session_factory() as session:
            store = PostgresIntakeStore(session)
            existing = await store.existing_position(
                topic=topic, partition=partition, offset=offset
            )
            if existing is not None:
                await session.rollback()
                return existing

            workspace = await store.resolve_workspace(topic, active_only=True)
            if workspace is None:
                decoded = self._decode_for_failure(raw)
                result = await store.record_failure(
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    payload_hash=payload_hash,
                    payload=decoded,
                    outcome="unassigned",
                    reason_code="workspace_topic_unassigned",
                    reason_detail={"message": "topic is not bound to an active Workspace"},
                    workspace_id=None,
                )
                await self._publish_failure(result, topic, partition, offset, decoded)
                return result

            try:
                decoded = json.loads(raw)
                if not isinstance(decoded, dict):
                    raise ValueError("Kafka payload must be a JSON object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                failure_payload = self._decode_for_failure(raw)
                result = await store.record_failure(
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    payload_hash=payload_hash,
                    payload=failure_payload,
                    outcome="dead_letter",
                    reason_code="invalid_json",
                    reason_detail={"message": str(exc)},
                    workspace_id=workspace.id,
                )
                await self._publish_failure(result, topic, partition, offset, failure_payload)
                return result

            try:
                message = KafkaIncidentAlert.model_validate(decoded)
            except ValidationError as exc:
                result = await store.record_failure(
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    payload_hash=payload_hash,
                    payload=decoded,
                    outcome="dead_letter",
                    reason_code="schema_validation_failed",
                    reason_detail=_validation_detail(exc),
                    workspace_id=workspace.id,
                )
                await self._publish_failure(result, topic, partition, offset, decoded)
                return result

            return await store.persist_kafka(
                workspace_id=workspace.id,
                topic=topic,
                partition=partition,
                offset=offset,
                payload_hash=payload_hash,
                incident=normalize_kafka(message),
            )

    async def replay(self, *, dead_letter_id: int, raw: bytes) -> IntakeResult:
        """Replay one durable DLQ row only through the current validator."""

        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Kafka payload must be a JSON object")
        message = KafkaIncidentAlert.model_validate(decoded)
        payload_hash = hashlib.sha256(raw).hexdigest()
        async with self._session_factory() as session:
            store = PostgresIntakeStore(session)
            dead_letter = await session.get(DeadLetter, dead_letter_id, with_for_update=True)
            if dead_letter is None:
                raise ValueError("dead letter not found")
            if dead_letter.replayed:
                raise ValueError("dead letter was already replayed")
            if dead_letter.partition is None or dead_letter.offset is None:
                raise ValueError("dead letter has no Kafka position")
            workspace = await store.resolve_workspace(dead_letter.topic, active_only=True)
            if workspace is None or workspace.id != dead_letter.workspace_id:
                raise ValueError("dead letter Workspace is not active")
            event_id = await session.scalar(
                select(IngestionEvent.id).where(
                    IngestionEvent.topic == dead_letter.topic,
                    IngestionEvent.partition == dead_letter.partition,
                    IngestionEvent.offset == dead_letter.offset,
                    IngestionEvent.dead_letter_id == dead_letter.id,
                )
            )
            if event_id is None:
                raise ValueError("dead letter ingestion event is missing")
            return await store.persist_kafka(
                workspace_id=workspace.id,
                topic=dead_letter.topic,
                partition=dead_letter.partition,
                offset=dead_letter.offset,
                payload_hash=payload_hash,
                incident=normalize_kafka(message),
                replay_event_id=event_id,
                replay_dead_letter_id=dead_letter.id,
            )

    @staticmethod
    def _decode_for_failure(raw: bytes) -> dict[str, str]:
        return {"raw": raw.decode("utf-8", "replace")}

    async def _publish_failure(
        self,
        result: IntakeResult,
        source_topic: str,
        partition: int,
        offset: int,
        payload: Any,
    ) -> None:
        if self._publisher is None or result.outcome == "duplicate":
            return
        masked, _ = mask_failure_payload(payload)
        envelope = {
            "source": {"topic": source_topic, "partition": partition, "offset": offset},
            "outcome": result.outcome,
            "dead_letter_id": result.dead_letter_id,
            "payload_masked": masked,
        }
        target = (
            KAFKA_DEAD_LETTER_TOPIC
            if result.outcome == "dead_letter"
            else KAFKA_UNASSIGNED_TOPIC
        )
        try:
            await self._publisher.send_and_wait(
                target,
                json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                key=f"{source_topic}:{partition}:{offset}".encode("utf-8"),
            )
        except Exception:  # noqa: BLE001 - PostgreSQL remains the durable DLQ
            logger.exception(
                "failed to mirror durable dead letter id=%s to Kafka", result.dead_letter_id
            )


async def _active_topics(session_factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Workspace.ingestion_topic)
                    .where(Workspace.ingestion_state == "active")
                    .order_by(Workspace.ingestion_topic)
                )
            ).scalars()
        )


async def process_record(
    record: ConsumerRecord,
    *,
    handler: RecordHandler,
    committer: OffsetCommitter,
) -> IntakeResult:
    """Persist one record, then and only then advance its consumer offset."""

    result = await handler.handle(
        topic=record.topic,
        partition=record.partition,
        offset=record.offset,
        raw=record.value,
    )
    await committer.commit(
        {
            TopicPartition(record.topic, record.partition): OffsetAndMetadata(
                record.offset + 1, ""
            )
        }
    )
    return result


async def main() -> None:
    """Consume active Workspace topics with persistence-before-offset semantics."""

    security = kafka_security_kwargs()
    consumer = AIOKafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=KAFKA_CONSUMER_GROUP_ID,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        **security,
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        **security,
    )
    await consumer.start()
    await producer.start()
    handler = KafkaIntakeHandler(publisher=producer)
    subscribed: list[str] = []
    try:
        while True:
            topics = await _active_topics(AsyncSessionLocal)
            ACTIVE_WORKSPACES.set(len(topics))
            CONSUMER_HEARTBEAT.set(time.time())
            if topics != subscribed:
                consumer.subscribe(topics=topics)
                subscribed = topics
                logger.info("subscribed to %d active Workspace topics", len(topics))
            if not topics:
                await asyncio.sleep(KAFKA_SUBSCRIPTION_REFRESH_SECONDS)
                continue

            batches = await consumer.getmany(
                timeout_ms=1000,
                max_records=KAFKA_BATCH_MAX_RECORDS,
            )
            for topic_partition, records in batches.items():
                for record in records:
                    try:
                        result = await process_record(
                            record,
                            handler=handler,
                            committer=consumer,
                        )
                        logger.info(
                            "processed %s:%s:%s outcome=%s",
                            record.topic,
                            record.partition,
                            record.offset,
                            result.outcome,
                        )
                    except Exception:  # noqa: BLE001 - leave offset uncommitted for redelivery
                        logger.exception(
                            "transient intake failure at %s:%s:%s",
                            topic_partition.topic,
                            topic_partition.partition,
                            record.offset,
                        )
                        await asyncio.sleep(1)
                        break
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
