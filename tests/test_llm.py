"""Unit tests for the LLM key resolution helper."""

from __future__ import annotations

import os

from incident_trace.engine.llm import resolve_api_key


def test_resolve_api_key_literal_passthrough():
    assert resolve_api_key("sk-literal-key") == "sk-literal-key"


def test_resolve_api_key_env(monkeypatch):
    monkeypatch.setenv("IT_TEST_LLM_KEY", "secret-from-env")
    assert resolve_api_key("env://IT_TEST_LLM_KEY") == "secret-from-env"


def test_resolve_api_key_env_missing_returns_empty():
    os.environ.pop("IT_TEST_LLM_MISSING", None)
    assert resolve_api_key("env://IT_TEST_LLM_MISSING") == ""
