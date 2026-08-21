"""Pytest configuration.

``pytest-asyncio`` gives each test a fresh event loop, but the module-level async
engine keeps a connection pool whose asyncpg connections are bound to the loop
they were opened on. Without intervention, a connection opened in test N would
be reused in test N+1's loop and asyncpg raises
``Future attached to a different loop``. Disposing the pool after every test
forces a fresh connection (in the current loop) on the next test, keeping the
DB-backed integration tests stable without a dedicated test database.
"""

from __future__ import annotations

import pytest_asyncio

from incident_trace.db.session import engine


@pytest_asyncio.fixture(autouse=True)
async def _reset_engine_pool():
    yield
    # Close pooled connections so the next test's new event loop opens fresh
    # ones. Harmless when no connection was opened (e.g. pure unit tests).
    await engine.dispose()
