"""Unit tests for human-hint injection into the analysis prompt."""

from __future__ import annotations

from lode.engine.runner import _build_prompts


class _FakeAlert:
    title = "Payment latency"
    level = "CRITICAL"
    env = "prod"
    error_message = "p99>2s"
    fields = {"orderId": "1"}


def test_human_hints_injected_as_data_not_commands() -> None:
    _system, user = _build_prompts(
        _FakeAlert(),
        None,
        [],
        [],
        None,
        human_hints="- check the payment gateway timeout",
    )
    assert "HUMAN_HINTS" in user
    assert "payment gateway timeout" in user
    # The model is told explicitly not to obey instructions inside the hints.
    assert "not commands" in user


def test_no_human_hints_section_when_none() -> None:
    _system, user = _build_prompts(_FakeAlert(), None, [], [], None)
    assert "HUMAN_HINTS" not in user
