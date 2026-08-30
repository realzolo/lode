"""Incident lifecycle rules and server-side action capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models import Incident, IncidentEvent

IncidentState = Literal["open", "acknowledged", "mitigated", "resolved", "closed"]
IncidentCommand = Literal[
    "acknowledge",
    "mitigate",
    "resolve",
    "close",
    "reopen",
    "start_investigation",
    "assign",
    "create_action",
    "review",
]

_TARGET_STATE: dict[IncidentCommand, IncidentState | None] = {
    "acknowledge": "acknowledged",
    "mitigate": "mitigated",
    "resolve": "resolved",
    "close": "closed",
    "reopen": "open",
    "start_investigation": None,
    "assign": None,
    "create_action": None,
    "review": None,
}

_ALLOWED_TRANSITIONS: dict[IncidentState, frozenset[IncidentState]] = {
    "open": frozenset({"acknowledged", "mitigated", "resolved"}),
    "acknowledged": frozenset({"mitigated", "resolved"}),
    "mitigated": frozenset({"open", "resolved"}),
    "resolved": frozenset({"open", "closed"}),
    "closed": frozenset(),
}


class IncidentLifecycleError(ValueError):
    """A stable client-safe incident lifecycle rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class IncidentActionCapability:
    action: IncidentCommand
    allowed: bool
    reason_code: str | None = None


def allowed_actions(*, state: str, can_respond: bool) -> tuple[IncidentActionCapability, ...]:
    """Return the sole UI action contract for an incident and its current actor."""

    if state not in _ALLOWED_TRANSITIONS:
        raise IncidentLifecycleError("incident_state_invalid", "Incident state is invalid.")
    commands: tuple[IncidentCommand, ...] = (
        "acknowledge",
        "mitigate",
        "resolve",
        "close",
        "reopen",
        "start_investigation",
        "assign",
        "create_action",
        "review",
    )
    values: list[IncidentActionCapability] = []
    for command in commands:
        target = _TARGET_STATE[command]
        if not can_respond:
            values.append(IncidentActionCapability(command, False, "responder_permission_required"))
        elif target is None:
            values.append(
                IncidentActionCapability(
                    command,
                    state != "closed",
                    None if state != "closed" else "incident_closed",
                )
            )
        elif target in _ALLOWED_TRANSITIONS[state]:
            values.append(IncidentActionCapability(command, True))
        else:
            values.append(IncidentActionCapability(command, False, "transition_not_allowed"))
    return tuple(values)


async def transition_incident(
    session: AsyncSession,
    *,
    incident: Incident,
    command: Literal["acknowledge", "mitigate", "resolve", "close", "reopen"],
    actor_id: int,
    reason: str,
    expected_state_version: int,
) -> Incident:
    """Apply one audited compare-and-swap incident state transition."""

    if incident.state_version != expected_state_version:
        raise IncidentLifecycleError(
            "incident_state_conflict", "Incident changed; reload it before applying another action."
        )
    target = _TARGET_STATE[command]
    assert target is not None
    if target not in _ALLOWED_TRANSITIONS.get(incident.state, frozenset()):
        raise IncidentLifecycleError(
            "incident_transition_invalid", "This action is not valid for the incident state."
        )
    now = datetime.now(UTC)
    previous = incident.state
    incident.state = target
    incident.state_changed_at = now
    incident.state_version += 1
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            event_type="state_changed",
            actor_id=actor_id,
            payload={
                "command": command,
                "from_state": previous,
                "to_state": target,
                "reason": reason,
                "state_version": incident.state_version,
            },
        )
    )
    return incident
