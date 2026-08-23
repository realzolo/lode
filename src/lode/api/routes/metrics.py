"""Prometheus scrape endpoint.

``GET /metrics`` exposes the process-wide registry in the text format the
Prometheus server expects. It is intentionally open (no bearer token) so a
cluster-scoped scrape job can pull it; restrict the path at the ingress /
network layer if finer control is needed.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
