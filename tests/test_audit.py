"""Hermetic tests for the append-only audit helper (no database required)."""

from __future__ import annotations

import pytest

from lode.api.audit import audit_action, get_request_id, record_audit_event
from lode.db.models.intake import AuditEvent


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = len(self.added)


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
    assert isinstance(event.request_id, str)
    assert session.added == [event]


@pytest.mark.asyncio
async def test_audit_action_never_raises_on_failure():
    class _BoomSession:
        def add(self, obj):
            raise RuntimeError("db down")

        async def flush(self):  # pragma: no cover - defensive
            pass

    # Must not propagate even when the underlying write fails.
    await audit_action(_BoomSession(), action="x", actor_id=1)
    assert get_request_id()  # contextvar is always available
