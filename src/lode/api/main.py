"""FastAPI application entry point.

On startup the database migrations are executed automatically (Alembic), so a
fresh deployment is always schema-current before it starts serving traffic.

Production hardening in this module:
  * CORS origins come from ``LODE_CORS_ORIGINS`` (not hard-coded).
  * A request-id is assigned to every request and echoed back in the response.
  * All errors return a consistent JSON envelope ``{"error": {"code", "message"}}``.
  * Every business router requires a valid bearer token (``require_user``);
    only ``/health``, ``/``, ``/docs``, ``/openapi.json`` and ``/auth/*`` are open.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from lode.api.deps import require_user
from lode.api.routes.alerts import router as alerts_router
from lode.api.routes.analyses import router as analyses_router
from lode.api.routes.applications import router as applications_router
from lode.api.routes.auth import router as auth_router
from lode.api.routes.health import router as health_router
from lode.api.routes.invites import router as invites_router
from lode.api.routes.queries import router as queries_router
from lode.api.routes.memories import router as memories_router
from lode.api.routes.settings import router as settings_router
from lode.api.routes.users import router as users_router
from lode.config import settings
from lode.migrations import run_migrations

# Per-request id, exposed to every logger via a logging filter. Falls back to
# "-" when code logs outside of an active HTTP request (engine, migrations, cron).
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


_log_handler = logging.StreamHandler()
_log_handler.addFilter(RequestIdFilter())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
    handlers=[_log_handler],
)
logger = logging.getLogger("lode.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> None:
    # Auto-execute database migrations before accepting traffic.
    await asyncio.get_running_loop().run_in_executor(None, run_migrations)
    yield


app = FastAPI(
    title="Lode",
    version="0.1.0",
    description="AI-powered production incident root-cause analysis platform",
    lifespan=lifespan,
)

# M6 hardening: rate limiting + baseline security headers. CORS is added last
# (below) so it remains the outermost layer and still stamps CORS headers on
# rate-limited (429) responses.
from lode.api.rate_limit import HardeningMiddleware, RateLimiter  # noqa: E402

app.add_middleware(
    HardeningMiddleware,
    limiter=RateLimiter(settings.rate_limit_per_minute),
    enabled=settings.rate_limit_enabled,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = rid
    token = _request_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        _request_id.reset(token)
    response.headers["x-request-id"] = rid
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.status_code, "message": exc.detail}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Drop the non-serializable ``ctx`` (which holds the raw raised exception,
    # e.g. a ValueError from a model_validator) so the 422 payload itself never
    # fails to serialize. Keep loc / msg / type which are what clients need.
    raw = exc.errors()
    details = [
        {k: v for k, v in err.items() if k not in ("ctx", "input")}
        for err in raw
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": 422,
                "message": "validation error",
                "details": details,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": 500, "message": "internal server error"}},
    )


# Open routes.
app.include_router(health_router)
app.include_router(auth_router)

# Protected business routes (require a valid bearer token).
_protected = [Depends(require_user)]
app.include_router(analyses_router, dependencies=_protected)
app.include_router(applications_router, dependencies=_protected)
app.include_router(memories_router, dependencies=_protected)
app.include_router(alerts_router, dependencies=_protected)
app.include_router(settings_router, dependencies=_protected)
app.include_router(users_router, dependencies=_protected)
app.include_router(queries_router, dependencies=_protected)

# Invites: admin endpoints carry require_admin (which itself requires auth);
# the accept endpoint is intentionally left open so new users can onboard.
app.include_router(invites_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "lode", "status": "ok"}


# CORS is registered last so it stays the outermost middleware: it stamps
# CORS headers on every response, including rate-limited (429) ones.
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
