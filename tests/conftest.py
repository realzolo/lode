"""Pytest configuration.

The suite is allowed to run only against a disposable ``lode_test_*`` database.
The module-level async engine also needs its pool disposed between pytest event
loops so an asyncpg connection from one test is never reused by another.
"""

from __future__ import annotations

from importlib import import_module

import pytest_asyncio

from lode.development.isolated_database import require_isolated_database

require_isolated_database("pytest")
engine = import_module("lode.db.session").engine


@pytest_asyncio.fixture(autouse=True)
async def _reset_engine_pool():
    yield
    # Close pooled connections so the next test's new event loop opens fresh
    # ones. Harmless when no connection was opened (e.g. pure unit tests).
    await engine.dispose()
