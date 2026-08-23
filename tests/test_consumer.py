"""Broker-less tests for the Kafka consumer's validate/route/persist logic.

These exercise ``process_message`` without a live Kafka broker or database by
injecting a fake producer, an in-memory fake session, and a no-op scheduler.
The three routing branches (invalid JSON -> DLQ, schema failure -> DLQ, unknown
topic -> unassigned) and the happy path (Alert + Analysis persisted, analysis
scheduled) are covered.
"""

from __future__ import annotations

import json

import pytest

from lode.consumer import main as consumer_main


class FakeProducer:
    def __init__(self) -> None:
        self.sent: dict[str, list[bytes]] = {}

    async def send_and_wait(self, topic: str, value: bytes) -> None:
        self.sent.setdefault(topic, []).append(value)


class FakeSession:
    """Minimal async-session stand-in: records added objects and assigns ids."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self._next_id = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                self._next_id += 1
                setattr(obj, "id", self._next_id)

    async def commit(self) -> None:
        # Real SQLAlchemy flushes pending changes on commit; mimic that so the
        # generated primary key is available to callers afterwards.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                self._next_id += 1
                setattr(obj, "id", self._next_id)

    async def execute(self, stmt):
        # Minimal stub for the idempotency lookup (select in-flight Analysis by
        # dedupe_key). Defaults to "no in-flight analysis"; tests that need a
        # match monkeypatch this method on the instance.
        class _Scalars:
            def first(self):
                return None

            def all(self):
                return []

        class _Result:
            def scalars(self):
                return _Scalars()

            def scalar_one_or_none(self):
                return None

        return _Result()


VALID_MSG = {
    "schema_version": "1.1",
    "level": "CRITICAL",
    "title": "PaymentService latency spike",
    "env": "prod",
    "timestamp": "2026-01-01T00:00:00Z",
    "event_type": "deploy",
    "fields": {"error": "p99 > 2s"},
}


async def test_invalid_json_routed_to_dlq() -> None:
    producer = FakeProducer()
    await consumer_main.process_message("alert.prod.x", b"not-json", producer)
    assert "lode.dlq" in producer.sent
    assert "lode.unassigned" not in producer.sent


async def test_schema_validation_failure_routed_to_dlq() -> None:
    producer = FakeProducer()
    # Missing required fields -> ValidationError.
    bad = json.dumps({"title": "incomplete"}).encode()
    await consumer_main.process_message("alert.prod.x", bad, producer)
    assert "lode.dlq" in producer.sent


async def test_unassigned_topic_routed() -> None:
    producer = FakeProducer()
    captured = {}

    def fake_schedule(analysis_id: int) -> None:
        captured["ran"] = True

    async def fake_resolve(db, topic):
        return None

    saved = consumer_main.resolve_application_id
    consumer_main.resolve_application_id = fake_resolve
    try:
        await consumer_main.process_message(
            "alert.unknown",
            json.dumps(VALID_MSG).encode(),
            producer,
            schedule=fake_schedule,
        )
    finally:
        consumer_main.resolve_application_id = saved

    assert "lode.unassigned" in producer.sent
    assert "lode.dlq" not in producer.sent
    # No analysis should have been scheduled for an unmapped topic.
    assert "ran" not in captured


async def test_valid_message_persists_and_schedules() -> None:
    producer = FakeProducer()
    session = FakeSession()
    scheduled: dict[str, int] = {}

    def fake_schedule(analysis_id: int) -> None:
        scheduled["analysis_id"] = analysis_id

    async def fake_resolve(db, topic):
        return 7

    saved = consumer_main.resolve_application_id
    consumer_main.resolve_application_id = fake_resolve
    try:
        await consumer_main.process_message(
            "alert.prod.x",
            json.dumps(VALID_MSG).encode(),
            producer,
            session=session,
            schedule=fake_schedule,
        )
    finally:
        consumer_main.resolve_application_id = saved

    # Nothing should be produced for a well-formed, mapped message.
    assert producer.sent == {}
    # One Alert and one Analysis should be persisted.
    assert len(session.added) == 2
    alert, analysis = session.added
    assert alert.application_id == 7
    assert analysis.status == "pending"
    assert analysis.alert_id == alert.id
    # The analysis engine should have been scheduled with the new analysis id.
    assert scheduled["analysis_id"] == analysis.id


async def test_duplicate_in_flight_is_reused() -> None:
    producer = FakeProducer()
    session = FakeSession()
    scheduled: dict[str, int] = {}

    def fake_schedule(analysis_id: int) -> None:
        scheduled["analysis_id"] = analysis_id

    async def fake_resolve(db, topic):
        return 7

    async def fake_execute(stmt):
        # Simulate an existing in-flight (pending/running) analysis for the key.
        class _Scalars:
            def first(self):
                return 99

        class _Result:
            def scalars(self):
                return _Scalars()

            def scalar_one_or_none(self):
                return 99

        return _Result()

    session.execute = fake_execute  # type: ignore[assignment]
    saved = consumer_main.resolve_application_id
    consumer_main.resolve_application_id = fake_resolve
    try:
        await consumer_main.process_message(
            "alert.prod.x",
            json.dumps(VALID_MSG).encode(),
            producer,
            session=session,
            schedule=fake_schedule,
        )
    finally:
        consumer_main.resolve_application_id = saved

    # No Alert/Analysis should be created and the engine must not be scheduled.
    assert producer.sent == {}
    assert session.added == []
    assert "analysis_id" not in scheduled
