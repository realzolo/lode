"""Kafka consumer: validate alerts, dedupe, and persist an investigation.

Reliability contract (production-grade):

* Routing is purely topic-based: the required application ingestion topic is
  globally unique. Messages that fail validation go to the dead-letter
  topic; messages whose topic is not mapped to any application go to the
  unassigned topic.
* A message is **idempotent** at the Kafka level: ``ingestion_events`` has a
  unique ``(topic, partition, offset)`` triple, so a redelivered record can
  never create a second alert/investigation.
* An application-scoped dedupe key suppresses an *additional* alert for an
  incident that already has an active investigation, so the same error event
  is never investigated twice. A partial unique index on
  ``investigation_jobs`` is the database-level backstop for concurrent consumers.
* The consumer **creates a queued job and commits the Kafka offset
  immediately** after the persist transaction succeeds. It does NOT execute the
  investigation. Execution is the worker's job (``lode.worker``), which means a
  burst of alerts is absorbed by the queue and a crashed process loses nothing.
* The offset is committed only when ``process_message`` returns normally. A
  transient failure (DB/Kafka/DLQ unavailable) raises so the offset is NOT
  committed and the record is redelivered after a reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from aiokafka.structs import OffsetAndMetadata, TopicPartition
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lode.config import kafka_security_kwargs, settings
from lode.consumer.alert_schema import AlertMessage, normalize_alert_error
from lode.db.models.alert import Alert
from lode.db.models.application import (
    Application,
    ApplicationServiceBinding,
    ApplicationIngestionOffset,
    ApplicationIngestionRuntime,
    Service,
)
from lode.db.models.intake import DeadLetter, Incident, IngestionEvent
from lode.db.models.investigation import Investigation
from lode.db.models.platform_setting import PlatformSetting
from lode.ai_output import AI_OUTPUT_LANGUAGE_SETTING_KEY, normalize_ai_output_language
from lode.db.session import AsyncSessionLocal
from lode.engine.investigation_intake import create_investigation
from lode.metrics import (
    INVESTIGATIONS,
    CONSUMER_LAG,
    DEAD_LETTERS,
    MESSAGES_RECEIVED,
)

logger = logging.getLogger("lode.consumer")

ERROR_MAX_LENGTH = 500


@dataclass(frozen=True)
class ActiveBinding:
    application_id: int
    topic: str
    ingestion_version: int
    start_position: str


async def _await_assignment(consumer: AIOKafkaConsumer, timeout: float = 10.0) -> set:
    """Block until the group coordinator assigns at least one partition.

    Raises ``RuntimeError`` if an explicitly active topic has no partition after
    ``timeout``. Applications with no active bindings never start a consumer,
    so an idle control plane remains healthy.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        assigned = consumer.assignment()
        if assigned:
            return assigned
        await asyncio.sleep(0.25)
    raise RuntimeError(
        "active application topics matched 0 partitions; verify topic existence and Kafka ACLs"
    )


async def load_active_bindings() -> dict[str, ActiveBinding]:
    """Return the exact topic set currently enabled by the control plane."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    Application.id,
                    Application.ingestion_topic,
                    Application.ingestion_version,
                    Application.ingestion_start_position,
                )
                .where(Application.ingestion_state == "active")
            )
        ).all()
    return {
        topic: ActiveBinding(
            application_id=application_id,
            topic=topic,
            ingestion_version=ingestion_version,
            start_position=start_position or "earliest",
        )
        for application_id, topic, ingestion_version, start_position in rows
    }


async def _set_runtime(
    bindings: dict[str, ActiveBinding],
    assignments: set[TopicPartition],
    *,
    state: str,
    consumer_id: str | None = None,
    backlog: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    """Upsert consumer-observed state for active applications only."""
    counts: dict[str, int] = {}
    for tp in assignments:
        counts[tp.topic] = counts.get(tp.topic, 0) + 1

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        for topic, binding in bindings.items():
            runtime = await session.get(ApplicationIngestionRuntime, binding.application_id)
            if runtime is None:
                runtime = ApplicationIngestionRuntime(application_id=binding.application_id)
                session.add(runtime)
            assigned = counts.get(topic, 0)
            runtime.observed_state = "listening" if state == "listening" and assigned else state
            runtime.observed_version = binding.ingestion_version
            runtime.consumer_id = consumer_id
            runtime.assigned_partitions = assigned
            runtime.backlog = backlog.get(topic) if backlog is not None else runtime.backlog
            runtime.last_heartbeat_at = now
            runtime.last_error = error
        await session.commit()


async def _mark_bindings_error(bindings: dict[str, ActiveBinding], error: Exception) -> None:
    if not bindings:
        return
    await _set_runtime(
        bindings,
        set(),
        state="error",
        error=str(error)[:500],
    )


async def _report_lag(consumer: AIOKafkaConsumer) -> None:
    """Best-effort update of the consumer-lag gauge for assigned partitions."""
    tps = consumer.assignment()
    if not tps:
        return
    try:
        end = await consumer.end_offsets(list(tps))
        committed = await consumer.committed(list(tps))
    except Exception:  # noqa: BLE001 - lag is telemetry; never block intake on it
        return
    for tp in tps:
        hw = end.get(tp)
        pos = committed.get(tp)
        if hw is None or pos is None:
            continue
        CONSUMER_LAG.labels(topic=tp.topic, partition=tp.partition).set(hw - pos)


# Raised for transient failures where the Kafka offset must NOT be committed
# (the record will be redelivered after the consumer reconnects).
class IngestionTransientError(Exception):
    pass


class IngestionPausedError(IngestionTransientError):
    """A control-plane change raced an already fetched Kafka record.

    The caller must not commit the source offset. Recreating the subscription
    drops the inactive topic, leaving the record available for a future resume.
    """

    pass


async def _produce(
    producer: AIOKafkaProducer,
    topic: str,
    value: dict[str, Any],
    key: bytes | None = None,
) -> None:
    # ``key`` makes the DLQ write idempotent: a redelivered source record always
    # maps to the same DLQ partition, complementing the unique constraint on
    # ``dead_letters`` so a crash between produce and offset-commit cannot create
    # duplicate dead letters.
    await producer.send_and_wait(
        topic,
        json.dumps(value, ensure_ascii=False).encode("utf-8"),
        key=key,
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


async def resolve_application_id(
    session: AsyncSession, topic: str, *, active_only: bool = False
) -> int | None:
    stmt = select(Application.id).where(Application.ingestion_topic == topic)
    if active_only:
        stmt = stmt.where(Application.ingestion_state == "active")
    result = await session.execute(
        stmt
    )
    return result.scalar_one_or_none()


async def _dedupe_active_exists(
    session: AsyncSession, application_id: int, dedupe_key: str
) -> bool:
    """True if the incident already has an active canonical investigation."""
    result = await session.execute(
        select(Investigation.id)
        .where(Investigation.application_id == application_id)
        .where(Investigation.trigger_signature == dedupe_key)
        .where(Investigation.status.in_(["queued", "running"]))
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
    request_id: str | None,
) -> tuple[int, int]:
    """Atomically record the ingestion event, alert, investigation and queued job.

    Returns ``(investigation_id, job_id)``. Raises ``IntegrityError`` (caught by the
    caller and treated as a duplicate) if a redelivery or a concurrent consumer
    beat us to the unique ``ingestion_events`` / active ``investigation_jobs`` row.
    """
    now = datetime.now(timezone.utc)
    occurred_at = alert.occurred_at or now
    ie = IngestionEvent(
        application_id=application_id,
        topic=topic,
        partition=partition,
        offset=offset,
        producer_event_id=producer_event_id,
        payload_hash=payload_hash,
        status="accepted",
        request_id=request_id,
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
            first_seen_at=occurred_at,
            last_seen_at=occurred_at,
        )
        db.add(incident)
        await db.flush()
    else:
        incident.latest_alert_id = None
        incident.last_seen_at = occurred_at

    alert.incident_id = incident.id
    db.add(alert)
    await db.flush()

    setting = await db.get(PlatformSetting, AI_OUTPUT_LANGUAGE_SETTING_KEY)
    output_language = normalize_ai_output_language(setting.value if setting is not None else None)
    envelope = alert.raw_payload or {}
    message = AlertMessage.model_validate(envelope)
    normalized_error = normalize_alert_error(message)
    investigation, job = await create_investigation(
        db,
        application_id=application_id,
        trigger_signature=dedupe_key,
        source_type="kafka",
        title=alert.title,
        severity=alert.level,
        occurred_at=occurred_at,
        output_language=output_language,
        error_name=normalized_error.name,
        error_message=normalized_error.message,
        error_stack=normalized_error.stack,
        error_cause=normalized_error.cause,
        error_properties=normalized_error.properties,
        fields=alert.fields or {},
        service_name=message.service_name,
        environment=message.environment,
        request_id=str(message.request_id),
        deployment_sha=message.git_commit,
        application_version=None,
        source_metadata={
            "schema_version": envelope.get("schema_version"),
            "alert_id": envelope.get("alert_id"),
            "event": envelope.get("event"),
        },
        scope_sources={
            "service": "alert.service_name",
            "environment": "alert.environment",
            "deployment_sha": "alert.git_commit",
            "request_id": "alert.request_id",
            "topic": topic,
            "partition": str(partition) if partition is not None else None,
            "offset": str(offset) if offset is not None else None,
        },
        alert_id=alert.id,
        incident_id=incident.id,
    )
    incident.alert_count = (incident.alert_count or 0) + 1
    incident.latest_alert_id = alert.id
    await db.commit()
    return investigation.id, job.id


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
    # Key the DLQ message by its source coordinates so a redelivered source
    # record collapses onto the same DLQ message (idempotent routing).
    source_key = (
        f"{topic}:{partition}:{offset}".encode("utf-8")
        if partition is not None and offset is not None
        else None
    )
    await _produce(
        producer,
        settings.kafka_dlq_topic if kind == "dlq" else settings.kafka_unassigned_topic,
        {"topic": topic, "dedupe_key": dedupe_key, "raw": payload} if kind == "unassigned"
        else {"topic": topic, "error": reason, "raw": payload},
        key=source_key,
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
    require_active_binding: bool = False,
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

    dedupe_key = hashlib.sha256(
        f"{msg.service_name}:{msg.event}:{msg.request_id}".encode()
    ).hexdigest()

    if session is not None:
        return await _process_with_session(
            session, producer, topic, dedupe_key, msg, data, partition, offset,
            require_active_binding,
        )

    async with AsyncSessionLocal() as db:
        return await _process_with_session(
            db, producer, topic, dedupe_key, msg, data, partition, offset,
            require_active_binding,
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
    require_active_binding: bool,
) -> str:
    app_id = (
        await resolve_application_id(session, topic, active_only=True)
        if require_active_binding
        else await resolve_application_id(session, topic)
    )
    if app_id is None:
        if require_active_binding:
            raise IngestionPausedError(
                f"topic {topic!r} is no longer active; refreshing subscription"
            )
        MESSAGES_RECEIVED.labels(outcome="unassigned").inc()
        await _route_failure(
            producer, session, topic=topic, kind="unassigned",
            reason="topic not mapped to any application",
            payload={"topic": topic, "dedupe_key": dedupe_key, "raw": data},
            dedupe_key=dedupe_key, partition=partition, offset=offset,
        )
        return "unassigned"

    source_service = (
        await session.execute(
            select(Service.id)
            .join(
                ApplicationServiceBinding,
                ApplicationServiceBinding.service_id == Service.id,
            )
            .where(
                ApplicationServiceBinding.application_id == app_id,
                Service.service_name == msg.service_name,
                Service.state == "active",
            )
        )
    ).scalar_one_or_none()
    if source_service is None:
        MESSAGES_RECEIVED.labels(outcome="dlq").inc()
        await _route_failure(
            producer,
            session,
            topic=topic,
            kind="dlq",
            reason="alert source service is not bound to the application",
            payload=data,
            dedupe_key=dedupe_key,
            application_id=app_id,
            partition=partition,
            offset=offset,
        )
        return "dlq"

    normalized_error = normalize_alert_error(msg)
    alert = Alert(
        dedupe_key=dedupe_key,
        application_id=app_id,
        topic=topic,
        title=msg.event,
        level=msg.level_value,
        alert_id=msg.alert_id,
        occurred_at=msg.occurred_at,
        dedupe_ttl_seconds=None,
        error_log=msg.error.model_dump(),
        error_message=normalized_error.message[:ERROR_MAX_LENGTH],
        fields=msg.correlation.model_dump(exclude_none=True),
        raw_payload=msg.model_dump(mode="json"),
    )

    # Suppress an additional alert for an incident with an active investigation.
    if await _dedupe_active_exists(session, app_id, dedupe_key):
        MESSAGES_RECEIVED.labels(outcome="duplicate").inc()
        logger.info(
            "skipping duplicate active investigation for application_id=%s dedupe_key=%s",
            app_id, dedupe_key,
        )
        return "duplicate"

    try:
        investigation_id, _job_id = await _persist(
            session,
            application_id=app_id,
            topic=topic,
            partition=partition,
            offset=offset,
            dedupe_key=dedupe_key,
            alert=alert,
            producer_event_id=None,
            payload_hash=None,
            request_id=str(msg.request_id),
        )
    except IntegrityError:
        await session.rollback()
        return "duplicate"

    MESSAGES_RECEIVED.labels(outcome="persisted").inc()
    INVESTIGATIONS.labels(result="scheduled").inc()
    return "persisted"


async def _initialize_assigned_offsets(
    consumer: AIOKafkaConsumer,
    bindings: dict[str, ActiveBinding],
    assignments: set[TopicPartition],
) -> None:
    """Apply a first-start policy once per activation version and partition."""
    pending = [tp for tp in assignments if tp.topic in bindings]
    if not pending:
        return
    beginnings = await consumer.beginning_offsets(pending)
    ends = await consumer.end_offsets(pending)
    rows: dict[TopicPartition, ApplicationIngestionOffset] = {}
    async with AsyncSessionLocal() as session:
        for tp in pending:
            binding = bindings[tp.topic]
            key = (binding.application_id, binding.ingestion_version, tp.topic, tp.partition)
            row = await session.get(ApplicationIngestionOffset, key)
            if row is None:
                target = (
                    beginnings[tp]
                    if binding.start_position == "earliest"
                    else ends[tp]
                )
                row = ApplicationIngestionOffset(
                    application_id=binding.application_id,
                    ingestion_version=binding.ingestion_version,
                    topic=tp.topic,
                    partition=tp.partition,
                    start_position=binding.start_position,
                    target_offset=target,
                )
                session.add(row)
            rows[tp] = row
        await session.commit()

    to_initialize = {tp: row for tp, row in rows.items() if row.initialized_at is None}
    if not to_initialize:
        return
    for tp, row in to_initialize.items():
        consumer.seek(tp, row.target_offset)
    await consumer.commit(
        {tp: OffsetAndMetadata(row.target_offset, "") for tp, row in to_initialize.items()}
    )
    async with AsyncSessionLocal() as session:
        for tp in to_initialize:
            binding = bindings[tp.topic]
            key = (binding.application_id, binding.ingestion_version, tp.topic, tp.partition)
            row = await session.get(ApplicationIngestionOffset, key)
            if row is not None:
                row.initialized_at = datetime.now(UTC)
        await session.commit()


async def _report_runtime(
    consumer: AIOKafkaConsumer,
    bindings: dict[str, ActiveBinding],
) -> None:
    assignments = consumer.assignment()
    backlog: dict[str, int] = {}
    if assignments:
        ends = await consumer.end_offsets(list(assignments))
        for tp in assignments:
            position = await consumer.position(tp)
            lag = max(0, ends[tp] - position)
            backlog[tp.topic] = backlog.get(tp.topic, 0) + lag
            CONSUMER_LAG.labels(topic=tp.topic, partition=tp.partition).set(lag)
    await _set_runtime(
        bindings,
        assignments,
        state="listening",
        consumer_id=f"{settings.kafka_group_id}:{id(consumer)}",
        backlog=backlog,
    )


async def main() -> None:
    """Consume exactly the topics enabled by the application control plane."""
    bindings: dict[str, ActiveBinding] = {}
    while True:
        try:
            bindings = await load_active_bindings()
            if not bindings:
                await asyncio.sleep(settings.kafka_subscription_refresh_seconds)
                continue
            security_kwargs = kafka_security_kwargs()
            consumer = AIOKafkaConsumer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=settings.kafka_group_id,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                **security_kwargs,
            )
            consumer.subscribe(topics=sorted(bindings))
            producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                **security_kwargs,
            )
            await consumer.start()
            await producer.start()

            assigned = await _await_assignment(consumer)
            await _initialize_assigned_offsets(consumer, bindings, assigned)
            await _report_runtime(consumer, bindings)
            logger.info(
                "consumer started on %s for %d application topic(s), assigned %d partition(s): %s",
                settings.kafka_bootstrap_servers,
                len(bindings),
                len(assigned),
                sorted(f"{tp.topic}[{tp.partition}]" for tp in assigned),
            )

            processed_since_lag = 0
            loop = asyncio.get_running_loop()
            next_refresh = loop.time() + settings.kafka_subscription_refresh_seconds
            try:
                while True:
                    if loop.time() >= next_refresh:
                        refreshed = await load_active_bindings()
                        if not refreshed:
                            raise IngestionPausedError("all application ingestion is paused")
                        if set(refreshed) != set(bindings):
                            bindings = refreshed
                            consumer.subscribe(topics=sorted(bindings))
                            assigned = await _await_assignment(consumer)
                            await _initialize_assigned_offsets(consumer, bindings, assigned)
                        else:
                            bindings = refreshed
                        await _report_runtime(consumer, bindings)
                        next_refresh = loop.time() + settings.kafka_subscription_refresh_seconds

                    batch = await consumer.getmany(
                        timeout_ms=1000, max_records=settings.kafka_batch_max_records
                    )
                    if not batch:
                        continue
                    for _tp, records in batch.items():
                        for record in records:
                            try:
                                await process_message(
                                    record.topic, record.value, producer,
                                    record.partition, record.offset,
                                    require_active_binding=True,
                                )
                            except IngestionTransientError:
                                logger.exception(
                                    "transient intake failure on topic %s; "
                                    "pausing redelivery", record.topic,
                                )
                                raise
                            await consumer.commit()
                            processed_since_lag += 1
                            if processed_since_lag >= 100:
                                await _report_runtime(consumer, bindings)
                                processed_since_lag = 0
            finally:
                await consumer.stop()
                await producer.stop()
            return
        except KafkaConnectionError as exc:
            await _mark_bindings_error(bindings, exc)
            logger.warning("kafka unavailable, retrying in 5s: %s", exc)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except IngestionPausedError:
            logger.info("application ingestion changed; rebuilding subscription")
            await asyncio.sleep(0.1)
        except IngestionTransientError:
            logger.warning("transient intake error, reconnecting to redeliver")
            await asyncio.sleep(2)
        except Exception as exc:
            await _mark_bindings_error(bindings, exc)
            logger.exception("consumer error, retrying in 5s: %s", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
