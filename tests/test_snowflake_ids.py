from __future__ import annotations

import asyncio
import subprocess
import sys
from time import monotonic

import pytest
from sqlalchemy import text

from lode.config import settings
from lode.db.session import engine


async def next_ids(count: int) -> list[int]:
    async with engine.connect() as connection:
        return [
            int(await connection.scalar(text("SELECT next_lode_id()")))
            for _ in range(count)
        ]


@pytest.mark.asyncio
async def test_snowflake_ids_are_safe_compact_monotonic_and_connection_coordinated() -> None:
    batches = await asyncio.gather(*(next_ids(128) for _ in range(16)))
    values = [value for batch in batches for value in batch]

    assert len(values) == len(set(values))
    assert all(batch == sorted(batch) for batch in batches)
    assert all(10 <= len(str(value)) <= 16 for value in values)
    assert all(0 < value <= (2**52 - 1) for value in values)


@pytest.mark.asyncio
async def test_snowflake_ids_clamp_clock_rollback_and_wait_on_sequence_overflow() -> None:
    async with engine.connect() as connection:
        current_ms = int(
            await connection.scalar(
                text(
                    "SELECT floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint "
                    "- 1577836800000"
                )
            )
        )
        future_ms = current_ms + 30
        forced = (future_ms << 10) | 1023
        await connection.execute(
            text("SELECT setval('lode_snowflake_state', :value, true)"),
            {"value": forced},
        )
        started = monotonic()
        generated = int(await connection.scalar(text("SELECT next_lode_id()")))

    assert generated > forced
    assert generated >> 10 > future_ms
    assert monotonic() - started >= 0.005


def test_snowflake_ids_coordinate_across_independent_processes() -> None:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    script = """
import asyncio
import asyncpg
import sys

async def main():
    connection = await asyncpg.connect(sys.argv[1])
    print(await connection.fetchval("SELECT next_lode_id()"))
    await connection.close()

asyncio.run(main())
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, dsn],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(12)
    ]
    values: list[int] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        values.append(int(stdout.strip()))

    assert len(values) == len(set(values))
    assert all(0 < value <= (2**52 - 1) for value in values)
