"""Rate limiting + security-headers middleware (M6 hardening).

A small, dependency-free, in-memory fixed-window rate limiter plus a set of
baseline security response headers. The limiter is intentionally simple and
single-node: it is the right tool for protecting one API process against
abuse (and it is fully hermetic-testable). For a multi-instance deployment,
swap ``RateLimiter`` for a Redis-backed store and keep the same interface.

Keying
-----
Authenticated requests are bucketed per user (``user:<sub>`` from the HMAC
token); unauthenticated requests fall back to the client IP. This way a leaked
or brute-forced token is throttled independently of other traffic, and a
client with no token is still bounded by its IP.

The limiter exposes ``check(key)`` (synchronous, no ``await``) so the atomic
read-increment happens entirely within one event-loop step — no lock needed.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from lode.config import settings
from lode.security import decode_token

logger = logging.getLogger("lode.api.rate_limit")

# Routes that must never be throttled: health, docs, schema and the root.
DEFAULT_EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}

# Baseline hardening headers applied to every response (setdefault so they can
# be overridden upstream if a route needs looser policy).
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


class RateLimiter:
    """In-process fixed-window counter.

    ``store`` is injectable for tests; it maps ``key -> {"window": float,
    "count": int}``. ``now`` is injectable so tests can drive the clock without
    real sleeps.
    """

    def __init__(
        self,
        limit_per_minute: int,
        *,
        store: Optional[dict] = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = max(1, int(limit_per_minute))
        self._store = store if store is not None else {}
        self._now = now
        self._window = 60.0

    def _prune_expired(self, now: float) -> None:
        """Drop entries whose window has fully elapsed to bound memory growth."""
        deadline = now - self._window
        stale = [k for k, v in self._store.items() if v["window"] < deadline]
        for k in stale:
            del self._store[k]

    def check(self, key: str) -> tuple[bool, dict[str, int]]:
        """Return ``(allowed, headers)`` and increment the bucket atomically.

        ``headers`` carries ``limit`` / ``remaining`` / ``reset_in`` (seconds)
        for the ``X-RateLimit-*`` / ``Retry-After`` response headers.
        """
        now = self._now()
        if len(self._store) > 4096:
            self._prune_expired(now)
        window_start = int(now // self._window) * self._window
        bucket = self._store.get(key)
        if bucket is None or bucket["window"] != window_start:
            bucket = self._store[key] = {"window": window_start, "count": 0}
        if bucket["count"] >= self.limit:
            reset_in = int((window_start + self._window - now)) + 1
            return False, {
                "limit": self.limit,
                "remaining": 0,
                "reset_in": max(0, reset_in),
            }
        bucket["count"] += 1
        remaining = self.limit - bucket["count"]
        reset_in = int((window_start + self._window - now)) + 1
        return True, {
            "limit": self.limit,
            "remaining": max(0, remaining),
            "reset_in": max(0, reset_in),
        }


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def default_key_func(request: Request) -> str:
    """Bucket authenticated callers by user id, everyone else by IP."""
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
        try:
            claims = decode_token(token, settings.secret_key)
            sub = claims.get("sub")
            if isinstance(sub, int):
                return f"user:{sub}"
        except Exception:  # noqa: BLE001 - unauthenticated/forged token -> IP bucket
            pass
    return f"ip:{_client_ip(request)}"


def _set_security_headers(response: Response) -> None:
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)


class HardeningMiddleware:
    """Combined rate-limit + security-header middleware (ASGI, no BaseHTTP).

    Implementing it directly as ASGI (rather than ``BaseHTTPMiddleware``) keeps
    the request/response plumbing explicit and avoids the streaming quirks of
    the helper base class. Rate limiting short-circuits before the inner app
    for throttled requests, and security headers are applied to both allowed
    and throttled responses.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: Optional[RateLimiter] = None,
        key_func: Callable[[Request], str] = default_key_func,
        exempt_paths: set[str] = DEFAULT_EXEMPT_PATHS,
        enabled: bool = True,
    ) -> None:
        self.app = app
        self.limiter = limiter or RateLimiter(settings.rate_limit_per_minute)
        self.key_func = key_func
        self.exempt_paths = exempt_paths
        self.enabled = enabled

    async def __call__(
        self, scope, receive: Callable[[], Awaitable[bytes]], send: Callable[[dict], Awaitable[None]]
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path

        if not self.enabled or path in self.exempt_paths:
            response_started = {}

            async def _send_no_limit(message):
                if message["type"] == "http.response.start":
                    response_started["headers"] = message.get("headers", [])
                await send(message)

            await self.app(scope, receive, _send_no_limit)
            return

        allowed, h = self.limiter.check(self.key_func(request))
        if not allowed:
            body = (
                b'{"error":{"code":429,"message":"rate limit exceeded"}}'
            )
            headers = {
                "content-type": "application/json",
                "retry-after": str(h["reset_in"]),
                "x-ratelimit-limit": str(h["limit"]),
                "x-ratelimit-remaining": "0",
            }
            resp = Response(content=body, status_code=429, headers=headers)
            # Apply security headers to the throttle response too.
            _set_security_headers(resp)
            await resp(scope, receive, send)
            return

        # Allowed: invoke the inner app, then stamp rate-limit + security headers
        # onto the response by intercepting http.response.start.
        ratelimit_headers = [
            (b"x-ratelimit-limit", str(h["limit"]).encode()),
            (b"x-ratelimit-remaining", str(h["remaining"]).encode()),
        ]
        security_items = [
            (k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()
        ]

        async def _send(message):
            if message["type"] == "http.response.start":
                existing = {(k.lower()): v for k, v in message.get("headers", [])}
                for k, v in ratelimit_headers + security_items:
                    if k not in existing:
                        message.setdefault("headers", []).append((k, v))
            await send(message)

        await self.app(scope, receive, _send)
