"""Hermetic tests for the append-only audit helper (no database required).

``audit_action`` opens and commits its own session, so we monkeypatch the
session factory with an in-memory fake to exercise the write/commit path without
a real database. ``record_audit_event`` is tested directly against a fake
session.
"""

from __future__ import annotations

import pytest

from lode.api import audit
from lode.api.audit import audit_action, get_request_id, record_audit_event
from lode.db.models.intake import AuditEvent


class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = len(self.added)

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_maker(session: _FakeSession):
    def _make():
        return session

    return _make


@pytest.mark.asyncio
async def test_record_audit_event_writes_row():
    session = _FakeSession()
    event = await record_audit_event(
        session,
        action="query.execute",
        actor_id=7,
        target_type="application",
        target_id="3",
        application_id=3,
        result="ok",
        detail={"tables": ["orders"]},
    )
    assert isinstance(event, AuditEvent)
    assert event.action == "query.execute"
    assert event.actor_id == 7
    assert event.target_type == "application"
    assert event.target_id == "3"
    assert event.application_id == 3
    assert event.result == "ok"
    assert event.detail == {"tables": ["orders"]}
    assert event.request_id is None
    assert session.added == [event]


@pytest.mark.asyncio
async def test_audit_action_writes_and_commits_row(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(audit, "AsyncSessionLocal", _fake_maker(session))
    await audit_action(
        action="application.create",
        actor_id=1,
        target_type="application",
        target_id="5",
        application_id=5,
    )
    assert len(session.added) == 1
    assert isinstance(session.added[0], AuditEvent)
    assert session.added[0].action == "application.create"
    assert session.added[0].actor_id == 1
    # Independent commit — the audit is durable regardless of the caller's txn.
    assert session.committed is True


@pytest.mark.asyncio
async def test_audit_action_never_raises_on_commit_failure(monkeypatch):
    class _BoomSession(_FakeSession):
        async def commit(self):
            raise RuntimeError("db down")

    session = _BoomSession()
    monkeypatch.setattr(audit, "AsyncSessionLocal", _fake_maker(session))
    # Must not propagate even when the underlying commit fails.
    await audit_action(action="x", actor_id=1)
    assert get_request_id() is None


@pytest.mark.asyncio
async def test_audit_action_never_raises_on_connect_failure(monkeypatch):
    def _boom_maker():
        raise RuntimeError("cannot connect")

    monkeypatch.setattr(audit, "AsyncSessionLocal", _boom_maker)
    # A connection failure must be swallowed, never break the observed operation.
    await audit_action(action="y", actor_id=2)
    assert get_request_id() is None
