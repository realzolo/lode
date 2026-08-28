"""FastAPI application entry point.

On startup the database migrations are executed automatically (Alembic), so a
fresh deployment is always schema-current before it starts serving traffic.

Production hardening in this module:
  * CORS accepts browser requests from any origin.
  * A request-id is assigned to every request and echoed back in the response.
  * All errors return a consistent JSON envelope ``{"error": {"code", "message"}}``.
  * Every business router requires a valid bearer token (``require_user``);
    only ``/health``, ``/``, ``/docs``, ``/openapi.json`` and ``/auth/*`` are open.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from lode.api.audit import _correlation_id
from lode.api.deps import require_admin, require_workbench_user
from lode.api.rate_limit import HardeningMiddleware, RateLimiter
from lode.api.routes.auth import router as auth_router
from lode.api.routes.control_plane import router as control_plane_router
from lode.api.routes.health import router as health_router
from lode.api.routes.investigations import (
    router as investigations_router,
    workbench_router,
)
from lode.api.routes.resources import router as resources_router
from lode.api.routes.users import router as users_router
from lode.migrations import run_migrations
from lode.runtime_defaults import API_RATE_LIMIT_PER_MINUTE

# Note: ``_correlation_id`` lives in ``lode.api.audit`` so audit records and the
# logger share one source of truth. Re-exported here for the request middleware.


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get() or "-"
        return True


_log_handler = logging.StreamHandler()
_log_handler.addFilter(CorrelationIdFilter())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [%(correlation_id)s] %(message)s",
    handlers=[_log_handler],
)
logger = logging.getLogger("lode.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> None:
    # Auto-execute database migrations before accepting traffic.
    await asyncio.get_running_loop().run_in_executor(None, run_migrations)
    # Job recovery (expired leases, stranded queued work) is owned by the
    # worker process, which reclaims them on startup. The API must NOT mutate
    # ``running`` investigations here: a job legitimately in flight under the worker
    # would otherwise be wrongly marked failed.

    yield


app = FastAPI(
    title="Lode",
    version="0.1.0",
    description="AI-powered production incident root-cause analysis platform",
    lifespan=lifespan,
)

# Rate limiting and baseline security headers. CORS is added last so it remains
# outermost and stamps CORS headers on rate-limited responses.
app.add_middleware(
    HardeningMiddleware,
    limiter=RateLimiter(API_RATE_LIMIT_PER_MINUTE),
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    try:
        incoming = uuid.UUID(request.headers.get("x-request-id", ""))
        rid = str(incoming) if incoming.version == 4 else str(uuid.uuid4())
    except ValueError:
        rid = str(uuid.uuid4())
    request.state.correlation_id = rid
    token = _correlation_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        _correlation_id.reset(token)
    response.headers["x-request-id"] = rid
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        business_code = exc.detail.get("code", exc.status_code)
        message = exc.detail.get("message", "request failed")
        details = {
            key: value for key, value in exc.detail.items() if key not in {"code", "message"}
        }
        error: dict[str, object] = {
            "code": business_code,
            "message": message,
        }
        if details:
            error["details"] = details
    else:
        error = {"code": exc.status_code, "message": exc.detail}
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Drop the non-serializable ``ctx`` (which holds the raw raised exception,
    # e.g. a ValueError from a model_validator) so the 422 payload itself never
    # fails to serialize. Keep loc / msg / type which are what clients need.
    raw = exc.errors()
    details = [{k: v for k, v in err.items() if k not in ("ctx", "input")} for err in raw]
    first = details[0] if details else {}
    location = first.get("loc")
    field = str(location[-1]) if isinstance(location, (list, tuple)) and location else "request"
    reason = str(first.get("msg", "is invalid"))
    if reason.startswith("Value error, "):
        reason = reason.removeprefix("Value error, ")
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": 422,
                "message": f"Invalid {field}: {reason}",
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

# Management and Workbench are separate authorization domains.  Router-level
# dependencies are intentional: they prevent a missed endpoint annotation from
# leaking the other portal's data.
_admin_routes = [Depends(require_admin)]
_workbench_routes = [Depends(require_workbench_user)]
app.include_router(control_plane_router, dependencies=_admin_routes)
app.include_router(users_router, dependencies=_admin_routes)
app.include_router(investigations_router, dependencies=_workbench_routes)
# Workspace discovery applies its own admin/all-workspaces vs ordinary-user/
# granted-workspaces filtering. The shared Workbench dependency permits the
# unrestricted system administrator as well as ordinary Workbench users.
app.include_router(workbench_router)
app.include_router(resources_router, dependencies=_workbench_routes)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "lode", "status": "ok"}


# CORS is registered last so it stays the outermost middleware: it stamps
# CORS headers on every response, including rate-limited (429) ones.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
