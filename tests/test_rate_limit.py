"""Hermetic tests for M6 rate limiting + hardening middleware.

These never touch the network or database. The ``RateLimiter`` decision logic
is tested directly with an injected clock; the middleware is exercised through a
fresh (non-global) ASGI app so its in-memory store cannot leak into the live
``app`` used by the rest of the suite.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from lode.api.rate_limit import (
    DEFAULT_EXEMPT_PATHS,
    HardeningMiddleware,
    RateLimiter,
    default_key_func,
)
from starlette.requests import Request


def _make_scope(headers, client_host="1.2.3.4", path="/x"):
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(k.encode(), v.encode()) for k, v in headers],
        "client": (client_host, 1234),
        "query_string": b"",
    }


def test_rate_limiter_allows_within_limit_then_denies():
    rl = RateLimiter(limit_per_minute=2, now=lambda: 1000.0)
    ok1, h1 = rl.check("k")
    ok2, h2 = rl.check("k")
    denied, hd = rl.check("k")
    assert ok1 and ok2 and not denied
    assert h1["limit"] == 2 and h1["remaining"] == 1
    assert h2["remaining"] == 0
    assert hd["remaining"] == 0
    assert hd["reset_in"] >= 0


def test_rate_limiter_resets_after_window():
    clock = {"t": 1000.0}
    rl = RateLimiter(limit_per_minute=1, now=lambda: clock["t"])
    assert rl.check("k")[0] is True
    assert rl.check("k")[0] is False  # same window, exhausted
    clock["t"] += 61.0  # advance past the 60s window
    assert rl.check("k")[0] is True  # new window, allowed again


def test_rate_limiter_is_per_key():
    rl = RateLimiter(limit_per_minute=1, now=lambda: 5.0)
    assert rl.check("a")[0] is True
    assert rl.check("b")[0] is True  # different key unaffected


def test_default_key_func_uses_user_id_for_bearer(monkeypatch):
    monkeypatch.setattr(
        "lode.api.rate_limit.decode_token",
        lambda token, secret: {"sub": 123},
    )
    scope = _make_scope([("authorization", "Bearer sometoken")])
    assert default_key_func(Request(scope)) == "user:123"


def test_default_key_func_falls_back_to_ip(monkeypatch):
    # Forged / missing token -> IP bucket, never raises.
    monkeypatch.setattr(
        "lode.api.rate_limit.decode_token",
        lambda token, secret: (_ for _ in ()).throw(ValueError("bad")),
    )
    scope = _make_scope([("authorization", "Bearer bad")], client_host="9.9.9.9")
    assert default_key_func(Request(scope)) == "ip:9.9.9.9"


def test_default_key_func_no_header_uses_ip():
    scope = _make_scope([], client_host="9.9.9.9")
    assert default_key_func(Request(scope)) == "ip:9.9.9.9"


def _build_app(limiter, *, exempt_paths=DEFAULT_EXEMPT_PATHS):
    def handler(request):
        return JSONResponse({"ok": True})

    inner = Starlette(
        routes=[
            Route("/", handler),
            Route("/health", handler),
            Route("/api/x", handler),
        ]
    )
    return HardeningMiddleware(
        inner,
        limiter=limiter,
        key_func=lambda r: "shared",
        exempt_paths=exempt_paths,
    )


def test_middleware_throttles_after_limit():
    app = _build_app(RateLimiter(limit_per_minute=2))
    client = TestClient(app)
    assert client.get("/api/x").status_code == 200
    assert client.get("/api/x").status_code == 200
    r = client.get("/api/x")
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == 429
    assert r.headers.get("retry-after")
    assert r.headers.get("x-ratelimit-limit") == "2"
    assert r.headers.get("x-ratelimit-remaining") == "0"


def test_middleware_exempt_paths_not_limited():
    app = _build_app(RateLimiter(limit_per_minute=1), exempt_paths={"/health"})
    client = TestClient(app)
    # /health is exempt: many requests all succeed even past the limit.
    for _ in range(5):
        assert client.get("/health").status_code == 200
    # And the strict limit still applies to non-exempt routes.
    assert client.get("/api/x").status_code == 200
    assert client.get("/api/x").status_code == 429


def test_middleware_security_headers_on_allowed():
    app = _build_app(RateLimiter(limit_per_minute=100))
    client = TestClient(app)
    r = client.get("/api/x")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "default-src 'none'" in (r.headers.get("content-security-policy") or "")


def test_middleware_security_headers_on_throttled():
    app = _build_app(RateLimiter(limit_per_minute=1))
    client = TestClient(app)
    client.get("/api/x")  # exhaust
    r = client.get("/api/x")  # 429
    assert r.status_code == 429
    assert r.headers.get("x-content-type-options") == "nosniff"
