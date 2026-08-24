"""Hermetic tests for production-hardening features (T6-T10).

Covers the bits that don't need a live database:

  * T6  deep health routes (``/health/live`` is dependency-free).
  * T7  Prometheus ``/metrics`` exposition.
  * T8  shared-memory TTL helper (``Memory.ttl_expiry``).
  * T9  LLM retry classification + graceful fallback to the heuristic.
  * T10 alert schema-version compatibility (accept 1.x, reject 2.x).
"""

from __future__ import annotations

import urllib.error
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lode.api.routes.metrics import router as metrics_router
from lode.consumer.alert_schema import AlertMessage
from lode.db.models.memory import Memory
from lode.engine.llm import _is_retryable, complete


# --- T6 deep health ---------------------------------------------------------

def test_health_live_is_dependency_free():
    from lode.api.routes.health import router as health_router

    app = FastAPI()
    app.include_router(health_router)
    client = TestClient(app)
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- T7 metrics -------------------------------------------------------------

def test_metrics_endpoint_exposes_lode_metrics():
    app = FastAPI()
    app.include_router(metrics_router)
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # The exposition format is text/plain with the metric family declarations.
    assert "lode_consumer_messages_total" in resp.text
    assert resp.headers["content-type"].startswith("text/plain")


# --- T8 memory TTL ----------------------------------------------------------

def test_ttl_expiry_future_and_timezone_aware():
    exp = Memory.ttl_expiry(90)
    assert exp is not None
    assert exp.tzinfo is not None
    assert exp > datetime.now(timezone.utc)


def test_ttl_zero_means_never_expires():
    assert Memory.ttl_expiry(0) is None


# --- T9 LLM retry classification -------------------------------------------

def test_retryable_classifies_http_5xx_and_network_as_transient():
    e500 = urllib.error.HTTPError(url="http://x", code=500, msg="", hdrs=None, fp=None)
    assert _is_retryable(e500) is True
    assert _is_retryable(urllib.error.URLError("dns boom")) is True


def test_retryable_rejects_http_4xx():
    e400 = urllib.error.HTTPError(url="http://x", code=400, msg="", hdrs=None, fp=None)
    e401 = urllib.error.HTTPError(url="http://x", code=401, msg="", hdrs=None, fp=None)
    assert _is_retryable(e400) is False
    assert _is_retryable(e401) is False


async def test_complete_falls_back_when_network_always_fails(monkeypatch):
    # No sleep so the test stays fast, but exercise the full retry+degrade path.
    from lode.engine import llm as llm_mod

    monkeypatch.setattr(llm_mod.settings, "llm_retry_base_delay", 0.0)

    class _Boom:
        async def __call__(self, fn):
            raise OSError("connection refused")

    monkeypatch.setattr(llm_mod, "_run_blocking", _Boom())
    # Missing config -> returns None immediately (no network attempt).
    assert await complete("sys", "user", None) is None
    # With a config it retries then degrades to None (heuristic fallback signal).
    cfg = type("C", (), {"provider": "openai", "base_url": "http://x",
                         "api_key_ref": "env://FAKE", "model": "m"})()
    assert await complete("sys", "user", cfg) is None


# --- T10 schema version (strict alert.v1, no backward-compat shims) ---------------

def test_schema_accepts_only_alert_v1():
    base = {
        "alert_id": "PB_x",
        "occurred_at": "2026-08-23T00:00:00Z",
        "event_type": "deploy_error",
        "level": "CRITICAL",
        "title": "oops",
        "dedupe_key": "alert:deploy_error:abc",
        "dedupe_ttl_seconds": 300,
    }
    # Only the exact current contract (alert.v1) is accepted. There are
    # deliberately no backward-compat shims: the old 1.1 envelope, a future
    # alert.v2, and any breaking contract are all rejected by the Literal type
    # (and therefore route to the DLQ).
    assert AlertMessage(schema_version="alert.v1", **base)
    for bad in ("1.1", "alert.v2", "1.0", "2.0", "1", "alert.v1.0"):
        with pytest.raises(Exception):
            AlertMessage(schema_version=bad, **base)
