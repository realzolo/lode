"""Auth dependencies.

``require_user`` validates the ``Authorization: Bearer <token>`` header and
returns the authenticated user id. The token is HMAC-signed (see
``incident_trace.security``), so a valid decode proves authenticity; routes
that need the full user object load it from the database themselves.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from incident_trace.config import settings
from incident_trace.security import decode_token


def require_user(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token, settings.secret_key)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")
    sub = claims.get("sub")
    if not isinstance(sub, int):
        raise HTTPException(status_code=401, detail="invalid token subject")
    return sub
