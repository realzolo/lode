"""Incident lifecycle command and capability rules."""

from __future__ import annotations

from lode.application.incident_lifecycle import allowed_actions


def test_open_incident_exposes_only_valid_responder_commands() -> None:
    capabilities = {
        value.action: value for value in allowed_actions(state="open", can_respond=True)
    }

    assert capabilities["acknowledge"].allowed
    assert capabilities["mitigate"].allowed
    assert capabilities["resolve"].allowed
    assert not capabilities["close"].allowed
    assert not capabilities["reopen"].allowed
    assert capabilities["start_investigation"].allowed
    assert capabilities["create_action"].allowed


def test_closed_incident_is_terminal_and_viewers_receive_no_mutating_capabilities() -> None:
    closed = allowed_actions(state="closed", can_respond=True)
    viewer = allowed_actions(state="resolved", can_respond=False)

    assert not any(value.allowed for value in closed)
    assert all(
        value.reason_code == "incident_closed" or value.reason_code == "transition_not_allowed"
        for value in closed
    )
    assert not any(value.allowed for value in viewer)
    assert {value.reason_code for value in viewer} == {"responder_permission_required"}
