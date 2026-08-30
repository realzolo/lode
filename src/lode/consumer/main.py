"""Strict Kafka `incident.alert.v1` transport adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRebalanceListener, TopicPartition
from aiokafka.structs import OffsetAndMetadata
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.application.intake import KafkaIncidentAlert, mask_failure_payload, normalize_kafka
from lode.config import kafka_security_kwargs, settings
from lode.db.models import (
    DeadLetter,
    IngestionEvent,
    Workspace,
    WorkspaceIngestionOffset,
    WorkspaceIngestionRuntime,
)
from lode.db.session import AsyncSessionLocal
from lode.infrastructure.intake_store import (
    IncidentCorrelationError,
    IntakeResult,
    PostgresIntakeStore,
)
from lode.metrics import ACTIVE_WORKSPACES, CONSUMER_HEARTBEAT
from lode.runtime_defaults import (
    KAFKA_BATCH_MAX_RECORDS,
    KAFKA_CONSUMER_GROUP_ID,
    KAFKA_DEAD_LETTER_TOPIC,
    KAFKA_SUBSCRIPTION_REFRESH_SECONDS,
    KAFKA_UNASSIGNED_TOPIC,
)

logger = logging.getLogger("lode.consumer")
CONSUMER_ID = f"{platform.node()}:{os.getpid()}"


class FailurePublisher(Protocol):
    async def send_and_wait(self, topic: str, value: bytes, key: bytes | None = None) -> Any: ...


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

    async def handle(self, *, topic: str, partition: int, offset: int, raw: bytes) -> IntakeResult:
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
            workspace_id = workspace.id

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
                    workspace_id=workspace_id,
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
                    workspace_id=workspace_id,
                )
                await self._publish_failure(result, topic, partition, offset, decoded)
                return result

            try:
                return await store.persist_kafka(
                    workspace_id=workspace_id,
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    payload_hash=payload_hash,
                    incident=normalize_kafka(message),
                )
            except IncidentCorrelationError as exc:
                await session.rollback()
                result = await store.record_failure(
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    payload_hash=payload_hash,
                    payload=decoded,
                    outcome="dead_letter",
                    reason_code="incident_correlation_failed",
                    reason_detail={"message": str(exc)},
                    workspace_id=workspace_id,
                )
                await self._publish_failure(result, topic, partition, offset, decoded)
                return result

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
            KAFKA_DEAD_LETTER_TOPIC if result.outcome == "dead_letter" else KAFKA_UNASSIGNED_TOPIC
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


async def _set_runtime_state(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    topics: list[str],
    assigned: set[TopicPartition],
    consumer_id: str,
) -> None:
    now = datetime.now(UTC)
    counts: dict[str, int] = {}
    for partition in assigned:
        counts[partition.topic] = counts.get(partition.topic, 0) + 1
    async with session_factory() as session:
        workspaces = (
            tuple(
                (
                    await session.execute(
                        select(Workspace).where(Workspace.ingestion_topic.in_(topics))
                    )
                ).scalars()
            )
            if topics
            else ()
        )
        for workspace in workspaces:
            runtime = await session.get(WorkspaceIngestionRuntime, workspace.id)
            if runtime is None:
                runtime = WorkspaceIngestionRuntime(workspace_id=workspace.id)
                session.add(runtime)
            partitions = counts.get(workspace.ingestion_topic, 0)
            if (
                runtime.observed_state == "error"
                and runtime.observed_version == workspace.ingestion_version
            ):
                runtime.last_heartbeat_at = now
                runtime.consumer_id = consumer_id
                continue
            runtime.observed_state = "listening" if partitions else "starting"
            runtime.observed_version = workspace.ingestion_version
            runtime.consumer_id = consumer_id
            runtime.assigned_partitions = partitions
            runtime.last_heartbeat_at = now
            runtime.last_error = None
        await session.commit()


async def _set_paused_runtime_states(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspaces = tuple(
            (
                await session.execute(
                    select(Workspace).where(Workspace.ingestion_state != "active")
                )
            ).scalars()
        )
        for workspace in workspaces:
            runtime = await session.get(WorkspaceIngestionRuntime, workspace.id)
            if runtime is None:
                runtime = WorkspaceIngestionRuntime(workspace_id=workspace.id)
                session.add(runtime)
            runtime.observed_state = "paused" if workspace.ingestion_state == "paused" else "idle"
            runtime.observed_version = workspace.ingestion_version
            runtime.consumer_id = None
            runtime.assigned_partitions = 0
            runtime.last_error = None
        await session.commit()


async def _reset_offset(
    consumer: AIOKafkaConsumer,
    partition: TopicPartition,
    position: str,
) -> int:
    if position == "earliest":
        return int((await consumer.beginning_offsets([partition]))[partition])
    return int((await consumer.end_offsets([partition]))[partition])


def _partition_resume_target(
    *,
    initialized_target: int | None,
    committed: int | None,
    activation_kind: str | None,
) -> int | None:
    if initialized_target is not None:
        return initialized_target if committed is None else max(initialized_target, committed)
    if activation_kind == "resume" and committed is not None:
        return committed
    return None


async def initialize_partition_positions(
    consumer: AIOKafkaConsumer,
    session_factory: async_sessionmaker[AsyncSession],
    partitions: set[TopicPartition],
    *,
    consumer_id: str = CONSUMER_ID,
) -> None:
    """Apply each Workspace's frozen activation position exactly once per generation."""

    if not partitions:
        return
    topics = sorted({item.topic for item in partitions})
    async with session_factory() as session:
        workspaces = {
            row.ingestion_topic: row
            for row in (
                await session.execute(
                    select(Workspace).where(
                        Workspace.ingestion_topic.in_(topics),
                        Workspace.ingestion_state == "active",
                    )
                )
            ).scalars()
        }
        counts: dict[int, int] = {}
        for partition in sorted(partitions, key=lambda item: (item.topic, item.partition)):
            workspace = workspaces.get(partition.topic)
            if workspace is None or workspace.ingestion_start_position is None:
                continue
            key = (
                workspace.id,
                workspace.ingestion_version,
                partition.topic,
                partition.partition,
            )
            initialized = await session.get(WorkspaceIngestionOffset, key)
            committed = await consumer.committed(partition)
            target = _partition_resume_target(
                initialized_target=None if initialized is None else initialized.target_offset,
                committed=None if committed is None else int(committed),
                activation_kind=workspace.ingestion_activation_kind,
            )
            if target is None:
                target = await _reset_offset(
                    consumer, partition, workspace.ingestion_start_position
                )
            if initialized is None:
                session.add(
                    WorkspaceIngestionOffset(
                        workspace_id=workspace.id,
                        ingestion_version=workspace.ingestion_version,
                        topic=partition.topic,
                        partition=partition.partition,
                        start_position=workspace.ingestion_start_position,
                        target_offset=target,
                        initialized_at=datetime.now(UTC),
                    )
                )
            consumer.seek(partition, target)
            counts[workspace.id] = counts.get(workspace.id, 0) + 1
        now = datetime.now(UTC)
        for workspace in workspaces.values():
            runtime = await session.get(WorkspaceIngestionRuntime, workspace.id)
            if runtime is None:
                runtime = WorkspaceIngestionRuntime(workspace_id=workspace.id)
                session.add(runtime)
            runtime.observed_state = "listening" if counts.get(workspace.id) else "starting"
            runtime.observed_version = workspace.ingestion_version
            runtime.consumer_id = consumer_id
            runtime.assigned_partitions = counts.get(workspace.id, 0)
            runtime.last_heartbeat_at = now
            runtime.last_error = None
        await session.commit()


class WorkspaceRebalanceListener(ConsumerRebalanceListener):
    def __init__(
        self,
        consumer: AIOKafkaConsumer,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.consumer = consumer
        self.session_factory = session_factory

    async def on_partitions_revoked(self, revoked: set[TopicPartition]) -> None:
        topics = sorted({item.topic for item in revoked})
        await _set_runtime_state(
            self.session_factory,
            topics=topics,
            assigned=set(),
            consumer_id=CONSUMER_ID,
        )

    async def on_partitions_assigned(self, assigned: set[TopicPartition]) -> None:
        try:
            await initialize_partition_positions(
                self.consumer,
                self.session_factory,
                assigned,
            )
        except Exception:
            logger.exception("failed to initialize Workspace Kafka positions")
            topics = sorted({item.topic for item in assigned})
            async with self.session_factory() as session:
                rows = tuple(
                    (
                        await session.execute(
                            select(Workspace).where(Workspace.ingestion_topic.in_(topics))
                        )
                    ).scalars()
                )
                for workspace in rows:
                    runtime = await session.get(WorkspaceIngestionRuntime, workspace.id)
                    if runtime is None:
                        runtime = WorkspaceIngestionRuntime(workspace_id=workspace.id)
                        session.add(runtime)
                    runtime.observed_state = "error"
                    runtime.observed_version = workspace.ingestion_version
                    runtime.consumer_id = CONSUMER_ID
                    runtime.assigned_partitions = 0
                    runtime.last_heartbeat_at = datetime.now(UTC)
                    runtime.last_error = "partition_initialization_failed"
                await session.commit()
            raise


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
        {TopicPartition(record.topic, record.partition): OffsetAndMetadata(record.offset + 1, "")}
    )
    return result


def _topic_subscription_pattern(topics: list[str]) -> str:
    """Match only the active topic inventory without requesting missing topics."""

    if not topics:
        raise ValueError("topic subscription requires at least one topic")
    return r"\A(?:" + "|".join(re.escape(topic) for topic in topics) + r")\Z"


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
    listener = WorkspaceRebalanceListener(consumer, AsyncSessionLocal)
    subscribed: list[str] = []
    try:
        while True:
            topics = await _active_topics(AsyncSessionLocal)
            ACTIVE_WORKSPACES.set(len(topics))
            CONSUMER_HEARTBEAT.set(time.time())
            if topics != subscribed:
                if topics:
                    consumer.subscribe(
                        pattern=_topic_subscription_pattern(topics),
                        listener=listener,
                    )
                else:
                    consumer.unsubscribe()
                subscribed = topics
                await _set_paused_runtime_states(AsyncSessionLocal)
                logger.info("subscribed to %d active Workspace topics", len(topics))
            if not topics:
                await asyncio.sleep(KAFKA_SUBSCRIPTION_REFRESH_SECONDS)
                continue

            await _set_runtime_state(
                AsyncSessionLocal,
                topics=topics,
                assigned=set(consumer.assignment()),
                consumer_id=CONSUMER_ID,
            )

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


def run() -> None:
    """Run the consumer process and treat operator interruption as a clean stop."""

    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("consumer stopped")


if __name__ == "__main__":
    run()
