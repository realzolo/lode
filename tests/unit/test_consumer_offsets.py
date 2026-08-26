"""Kafka offset ordering tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from lode.consumer.main import process_record
from lode.infrastructure.intake_store import IntakeResult


@dataclass
class Record:
    topic: str = "incident.test"
    partition: int = 2
    offset: int = 41
    value: bytes = b"{}"


class Committer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def commit(self, offsets: dict) -> None:
        self.calls.append(offsets)


class Handler:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def handle(self, **_kwargs) -> IntakeResult:
        if self.error is not None:
            raise self.error
        return IntakeResult(outcome="accepted", investigation_id=1)


async def test_offset_advances_only_after_durable_handler_success() -> None:
    committer = Committer()

    result = await process_record(Record(), handler=Handler(), committer=committer)

    assert result.outcome == "accepted"
    assert len(committer.calls) == 1
    topic_partition, offset = next(iter(committer.calls[0].items()))
    assert (topic_partition.topic, topic_partition.partition) == ("incident.test", 2)
    assert offset.offset == 42


async def test_transient_handler_failure_leaves_offset_uncommitted() -> None:
    committer = Committer()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await process_record(
            Record(),
            handler=Handler(RuntimeError("database unavailable")),
            committer=committer,
        )

    assert committer.calls == []
