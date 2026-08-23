"""Append-only audit logging for high-risk control-plane actions.

``audit_events`` is the immutable record of security- and data-sensitive
operations (DLQ replay, read-only query execution against a production replica,
analysis triggers, data-source mutations, …). Every such action records who did
it, on what, from where (request/trace id), and whether it succeeded.

The table is *append-only* by design: records are never updated or deleted by the
application (only the retention reaper may prune them). This module owns the
single ``record_audit_event`` entry point so the schema and the "what counts as
high-risk" decision live in one place.
"""

from __future__ import annotations

import contextvars
import logging

from lode.db.models.intake import AuditEvent

logger = logging.getLogger("lode.api.audit")

# Per-request id, shared with the logger via ``RequestIdFilter`` (see api/main.py).
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_request_id() -> str:
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
    trace_id: str | None = None,
) -> AuditEvent:
    """Append an audit record. Best-effort from the caller's perspective.

    The caller should wrap this in a ``try/except`` so a failure to write the
    audit row (e.g. a transient DB error) never aborts the underlying action —
    security logging must not be a hard dependency of the operation it observes.
    """
    event = AuditEvent(
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        target_type=target_type,
        target_id=target_id,
        application_id=application_id,
        request_id=get_request_id(),
        trace_id=trace_id,
        result=result,
        detail=detail,
    )
    session.add(event)
    await session.flush()
    return event


async def audit_action(session, *, action: str, **kwargs) -> None:
    """Best-effort variant of :func:`record_audit_event`.

    Never raises: a failure to persist the audit row (transient DB error, …) is
    logged but never propagated, so security logging can never abort the action
    it is observing.
    """
    try:
        await record_audit_event(session, action=action, **kwargs)
    except Exception:  # noqa: BLE001 - audit must never break the operation
        logger.exception("failed to record audit event %s", action)
