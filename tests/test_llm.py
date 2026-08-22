"""Unit tests for the LLM key resolution helper."""

from __future__ import annotations

import os

from lode.engine.llm import resolve_api_key


def test_resolve_api_key_literal_passthrough():
    assert resolve_api_key("sk-literal-key") == "sk-literal-key"


def test_resolve_api_key_env(monkeypatch):
    monkeypatch.setenv("LODE_TEST_LLM_KEY", "secret-from-env")
    assert resolve_api_key("env://LODE_TEST_LLM_KEY") == "secret-from-env"


def test_resolve_api_key_env_missing_returns_empty():
    os.environ.pop("LODE_TEST_LLM_MISSING", None)
    assert resolve_api_key("env://LODE_TEST_LLM_MISSING") == ""
