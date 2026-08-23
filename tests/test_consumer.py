"""Broker-less tests for the Kafka consumer's validate/route/dedupe/persist logic.

These exercise ``process_message`` without a live Kafka broker or database by
injecting a fake producer, an in-memory fake session, and a fake application
resolver. Covered branches: invalid JSON -> DLQ, schema failure -> DLQ, unknown
topic -> unassigned, happy path (event + incident + alert + analysis + queued
job persisted), and dedupe (an active analysis suppresses a second alert).
"""

from __future__ import annotations

import json

import pytest

from lode.consumer import main as consumer_main
from lode.db.models.intake import AnalysisJob, Incident, IngestionEvent


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
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                self._next_id += 1
                setattr(obj, "id", self._next_id)

    async def execute(self, stmt):
        # Default: no existing incident and no active analysis (fast path).
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
    status = await consumer_main.process_message(
        "alert.prod.x", b"not-json", producer, session=FakeSession()
    )
    assert status == "dlq"
    assert "lode.dlq" in producer.sent
    assert "lode.unassigned" not in producer.sent


async def test_schema_validation_failure_routed_to_dlq() -> None:
    producer = FakeProducer()
    bad = json.dumps({"title": "incomplete"}).encode()
    status = await consumer_main.process_message(
        "alert.prod.x", bad, producer, session=FakeSession()
    )
    assert status == "dlq"
    assert "lode.dlq" in producer.sent


async def test_unassigned_topic_routed() -> None:
    producer = FakeProducer()

    async def fake_resolve(session, topic):
        return None

    saved = consumer_main.resolve_application_id
    consumer_main.resolve_application_id = fake_resolve
    try:
        status = await consumer_main.process_message(
            "alert.unknown", json.dumps(VALID_MSG).encode(), producer, session=FakeSession()
        )
    finally:
        consumer_main.resolve_application_id = saved

    assert status == "unassigned"
    assert "lode.unassigned" in producer.sent
    assert "lode.dlq" not in producer.sent


async def test_valid_message_persists_and_queues_job() -> None:
    producer = FakeProducer()
    session = FakeSession()

    async def fake_resolve(session, topic):
        return 7

    saved = consumer_main.resolve_application_id
    consumer_main.resolve_application_id = fake_resolve
    try:
        status = await consumer_main.process_message(
            "alert.prod.x",
            json.dumps(VALID_MSG).encode(),
            producer,
            partition=0,
            offset=1,
            session=session,
        )
    finally:
        consumer_main.resolve_application_id = saved

    # Nothing is produced for a well-formed, mapped message.
    assert status == "persisted"
    assert producer.sent == {}

    types = {type(o) for o in session.added}
    assert IngestionEvent in types
    assert Incident in types
    assert AnalysisJob in types
    # Exactly one Alert and one Analysis were persisted (plus incident/job/ie).
    alerts = [o for o in session.added if type(o).__name__ == "Alert"]
    analyses = [o for o in session.added if type(o).__name__ == "Analysis"]
    assert len(alerts) == 1 and len(analyses) == 1
    assert alerts[0].application_id == 7
    assert analyses[0].status == "pending"
    assert analyses[0].alert_id == alerts[0].id
    assert analyses[0].incident_id is not None
    job = next(o for o in session.added if isinstance(o, AnalysisJob))
    assert job.status == "queued"
    assert job.incident_id == analyses[0].incident_id


async def test_duplicate_active_analysis_is_suppressed() -> None:
    producer = FakeProducer()
    session = FakeSession()

    async def fake_execute(stmt):
        # Simulate an existing active (pending/running) analysis for the key.
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

    async def fake_resolve(session, topic):
        return 7

    saved = consumer_main.resolve_application_id
    consumer_main.resolve_application_id = fake_resolve
    try:
        status = await consumer_main.process_message(
            "alert.prod.x",
            json.dumps(VALID_MSG).encode(),
            producer,
            partition=0,
            offset=2,
            session=session,
        )
    finally:
        consumer_main.resolve_application_id = saved

    # No Alert/Analysis/Job should be created and no Kafka message produced.
    assert status == "duplicate"
    assert producer.sent == {}
    assert session.added == []
