"""Append-only audit logging for high-risk control-plane actions.

``audit_events`` is the immutable record of security- and data-sensitive
operations (DLQ replay, read-only query execution against a production replica,
analysis triggers, data-source / user / model mutations, …). Every such action
records who did it, on what, from which request, and whether it
succeeded.

The table is *append-only* by design: records are never updated or deleted by the
application (only the retention reaper may prune them). This module owns the
single ``record_audit_event`` entry point so the schema and the "what counts as
high-risk" decision live in one place.

Durability contract
-------------------
:func:`audit_action` writes and **commits in its own session**, decoupled from
the caller's transaction. This guarantees the audit record survives even when
the business transaction rolls back or errors out — a failed attempt (e.g. a
rejected query, a login with bad credentials) is still recorded, which is exactly
what a security log is for. The write is strictly best-effort: a DB connection or
commit failure is logged and swallowed, never propagated, so security logging can
never break the operation it observes.
"""

from __future__ import annotations

import contextvars
import logging

from lode.db.models.intake import AuditEvent
from lode.db.session import AsyncSessionLocal

logger = logging.getLogger("lode.api.audit")

# Per-request id, shared with the logger via ``RequestIdFilter`` (see api/main.py).
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


async def record_audit_event(
    session,
    *,
    action: str,
    actor_id: int | None = None,
    actor_email: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    application_id: int | None = None,
    result: str = "ok",
    detail: dict | None = None,
) -> AuditEvent:
    """Append an audit record to the given ``session`` (flush only).

    Used when an audit row must ride along inside an existing transaction (e.g.
    a caller that wants the audit to roll back with the business change). The
    caller is responsible for committing ``session``. For the common, durable,
    best-effort case, prefer :func:`audit_action`, which commits independently.
    """
    event = AuditEvent(
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        target_type=target_type,
        target_id=target_id,
        application_id=application_id,
        request_id=get_request_id(),
        result=result,
        detail=detail,
    )
    session.add(event)
    await session.flush()
    return event


async def audit_action(*, action: str, **kwargs) -> None:
    """Best-effort audit write that commits in its own independent session.

    Never raises and never touches the caller's transaction: it opens a fresh
    session, appends the event, and commits it on its own. A connection/commit
    failure is logged but swallowed, so security logging can never abort the
    operation it is observing. Because it commits separately, the record is
    durable even when the observed action fails.
    """
    try:
        async with AsyncSessionLocal() as session:
            await record_audit_event(session, action=action, **kwargs)
            await session.commit()
    except Exception:  # noqa: BLE001 - audit must never break the operation
        logger.exception("failed to record audit event %s", action)
