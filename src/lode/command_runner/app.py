"""Minimal authenticated API for the isolated command runner."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
from time import time

from fastapi import FastAPI, Header, HTTPException

from lode.command_runner.executor import CommandExecutor, RunnerRejected
from lode.command_runner.protocol import SignedRunnerRequest, verify_request

app = FastAPI(title="Lode Isolated Command Runner", docs_url=None, redoc_url=None)
executor = CommandExecutor()
_seen: dict[str, int] = {}
_seen_lock = threading.Lock()


def _key() -> str:
    value = os.environ.get("LODE_COMMAND_RUNNER_KEY", "")
    if len(value.encode()) < 32:
        raise RuntimeError("LODE_COMMAND_RUNNER_KEY must contain at least 32 bytes")
    return value


def _require_enabled() -> None:
    value = os.environ.get("LODE_COMMAND_RUNNER_ENABLED", "true").lower()
    if value not in {"true", "false"}:
        raise RuntimeError("LODE_COMMAND_RUNNER_ENABLED must be true or false")
    if value == "false":
        raise HTTPException(status_code=503, detail="runner is disabled")


def _authenticate(envelope: SignedRunnerRequest) -> None:
    _require_enabled()
    try:
        verify_request(envelope, _key())
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="runner request rejected") from exc
    now = int(time())
    with _seen_lock:
        expired = [nonce for nonce, expiry in _seen.items() if expiry < now]
        for nonce in expired:
            del _seen[nonce]
        if envelope.request.nonce in _seen:
            raise HTTPException(status_code=409, detail="runner request replayed")
        _seen[envelope.request.nonce] = envelope.request.expires_at


@app.get("/health")
async def health() -> dict[str, str]:
    _require_enabled()
    _key()
    return {"status": "ok"}


@app.get("/catalog")
async def catalog(x_runner_signature: str = Header(default="")) -> dict[str, object]:
    _require_enabled()
    expected = hmac.new(_key().encode(), b"catalog", hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_runner_signature):
        raise HTTPException(status_code=403, detail="runner request rejected")
    try:
        return executor.catalog()
    except RunnerRejected as exc:
        raise HTTPException(status_code=503, detail="runner unavailable") from exc


@app.post("/preflight")
async def preflight(envelope: SignedRunnerRequest) -> dict[str, object]:
    _authenticate(envelope)
    try:
        return await executor.preflight(envelope.request.action)
    except RunnerRejected as exc:
        raise HTTPException(status_code=409, detail="runner action rejected") from exc


@app.post("/execute")
async def execute(envelope: SignedRunnerRequest):
    _authenticate(envelope)
    return await executor.execute(envelope.request.action)
