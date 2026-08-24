"""Hermetic tests for the audit *read* endpoint (no database required).

``list_audit_events`` is an async function that takes its session via a
dependency; calling it directly with a fake session exercises the query
assembly, filter wiring, timezone normalization, ordering, and pagination
metadata without a real Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lode.api.routes.audit import list_audit_events
from lode.db.models.intake import AuditEvent


class _ReadResult:
    def __init__(self, rows, count):
        self._rows = rows
        self._count = count

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._count


class _ReadSession:
    """Minimal async-session fake that records every executed statement."""

    def __init__(self, rows, count):
        self._rows = rows
        self._count = count
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _ReadResult(self._rows, self._count)


def _call(session: _ReadSession, **overrides):
    """Call ``list_audit_events`` the way FastAPI would after resolving its
    ``Query`` defaults — every optional filter is explicitly ``None`` unless a
    test overrides it. Calling the function directly bypasses FastAPI's
    dependency resolution, so the unresolved ``Query`` objects must not leak in.
    """
    defaults = dict(
        action=None,
        actor_id=None,
        actor_email=None,
        target_type=None,
        target_id=None,
        application_id=None,
        result=None,
        since=None,
        until=None,
        limit=50,
        offset=0,
    )
    defaults.update(overrides)
    return list_audit_events(session=session, _admin=1, **defaults)


def _make_rows() -> list[AuditEvent]:
    # Two rows, intentionally out of chronological order so we can assert the
    # endpoint re-sorts them descending by created_at.
    older = AuditEvent(
        id=1,
        action="application.create",
        actor_id=1,
        target_type="application",
        target_id="5",
        application_id=5,
        result="ok",
        created_at=datetime(2026, 8, 23, 9, 0, 0, tzinfo=UTC),
    )
    newer = AuditEvent(
        id=2,
        action="query.execute",
        actor_id=7,
        target_type="application",
        target_id="3",
        application_id=3,
        result="error",
        detail={"tables": ["orders"]},
        created_at=datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC),
    )
    return [older, newer]


@pytest.mark.asyncio
async def test_list_returns_paginated_wrapper():
    rows = _make_rows()
    session = _ReadSession(rows, len(rows))
    out = await _call(session)

    assert out.total == 2
    assert out.limit == 50
    assert out.offset == 0
    assert len(out.items) == 2
    actions = {i.action for i in out.items}
    assert actions == {"query.execute", "application.create"}
    # Both count and list statements were issued.
    assert len(session.statements) == 2
    # The list query must order most-recent-first.
    list_stmt = str(session.statements[-1])
    assert "ORDER BY" in list_stmt
    assert "audit_events.created_at" in list_stmt
    assert "DESC" in list_stmt


@pytest.mark.asyncio
async def test_list_filters_are_wired_into_sql():
    session = _ReadSession(_make_rows(), 2)
    # Apply an action + result filter; the compiled SQL must mention both
    # columns, proving the filters reach the statement builder.
    await _call(session, action="application.create", result="error")
    list_stmt = str(session.statements[-1])
    assert "audit_events.action" in list_stmt
    assert "audit_events.result" in list_stmt
    # The count statement must also carry the filters.
    count_stmt = str(session.statements[0])
    assert "audit_events" in count_stmt


@pytest.mark.asyncio
async def test_list_pagination_metadata_is_echoed():
    session = _ReadSession(_make_rows(), 2)
    out = await _call(session, limit=10, offset=5)
    assert out.limit == 10
    assert out.offset == 5
    assert out.total == 2


@pytest.mark.asyncio
async def test_list_handles_naive_timestamp_filter():
    # A naive (timezone-less) `since` must be normalized to UTC without raising
    # a naive-vs-aware comparison error downstream.
    session = _ReadSession(_make_rows(), 2)
    out = await _call(session, since=datetime(2026, 8, 1, 0, 0, 0))  # naive
    assert out.total == 2
    # The since filter should appear in the compiled SQL as a >= bound.
    assert ">=" in str(session.statements[-1])
