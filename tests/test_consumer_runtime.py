"""Database-backed consumer activation and runtime-state invariants."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from aiokafka import TopicPartition
from sqlalchemy.exc import IntegrityError

from lode.consumer.main import _set_runtime_state, _topic_subscription_pattern
from lode.db.models import Workspace, WorkspaceIngestionRuntime
from lode.db.session import AsyncSessionLocal


def _active_workspace(topic: str) -> Workspace:
    return Workspace(
        name="Consumer runtime",
        ingestion_topic=topic,
        ingestion_state="active",
        ingestion_version=1,
        ingestion_start_position="earliest",
        ingestion_activation_kind="start",
        ingestion_started_at=datetime.now(UTC),
    )


def test_topic_subscription_pattern_is_exact_and_escaped() -> None:
    pattern = re.compile(_topic_subscription_pattern(["events.orders.v1", "events+ops"]))

    assert pattern.fullmatch("events.orders.v1")
    assert pattern.fullmatch("events+ops")
    assert pattern.fullmatch("events-orders-v1") is None
    assert pattern.fullmatch("events.orders.v1.extra") is None
    with pytest.raises(ValueError, match="at least one"):
        _topic_subscription_pattern([])


async def test_database_rejects_an_incomplete_active_workspace() -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            Workspace(
                name="Invalid consumer runtime",
                ingestion_topic="consumer.invalid-active",
                ingestion_state="active",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_heartbeat_preserves_partition_initialization_error_until_recovery() -> None:
    topic = "consumer.runtime-error"
    async with AsyncSessionLocal() as session:
        workspace = _active_workspace(topic)
        session.add(workspace)
        await session.flush()
        session.add(
            WorkspaceIngestionRuntime(
                workspace_id=workspace.id,
                observed_state="error",
                observed_version=workspace.ingestion_version,
                consumer_id="old-consumer",
                assigned_partitions=0,
                last_error="partition_initialization_failed",
            )
        )
        await session.commit()
        workspace_id = workspace.id

    assigned = {TopicPartition(topic, 0)}
    await _set_runtime_state(
        AsyncSessionLocal,
        topics=[topic],
        assigned=assigned,
        consumer_id="new-consumer",
    )

    async with AsyncSessionLocal() as session:
        runtime = await session.get(WorkspaceIngestionRuntime, workspace_id)
        assert runtime is not None
        assert runtime.observed_state == "error"
        assert runtime.assigned_partitions == 0
        assert runtime.last_error == "partition_initialization_failed"
        runtime.observed_version = 0
        await session.commit()

    await _set_runtime_state(
        AsyncSessionLocal,
        topics=[topic],
        assigned=assigned,
        consumer_id="new-consumer",
    )

    async with AsyncSessionLocal() as session:
        recovered = await session.get(WorkspaceIngestionRuntime, workspace_id)
        assert recovered is not None
        assert recovered.observed_state == "listening"
        assert recovered.assigned_partitions == 1
        assert recovered.last_error is None
