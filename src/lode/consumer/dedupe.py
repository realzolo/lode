"""Recompute the Lark alert dedupe key.

This is a faithful port of ``buildAlertKey`` / ``buildFingerprint`` from the
business ``lark-alert.ts`` utility. The platform recomputes the same key so
for incident correlation. It is not an analysis route identifier; analysis runs
use an opaque public ID so business alert signatures do not leak into URLs.

Do NOT change hashing, separators, or normalization here without also
changing the source tool — the two must stay byte-for-byte identical.
"""

from __future__ import annotations

import hashlib
import json
import re

# Mirrors DEFAULT_FIELD_KEYS in lark-alert.ts (order matters).
DEFAULT_FIELD_KEYS: list[str] = [
    "orderId",
    "transactionId",
    "providerCode",
    "provider",
    "channelId",
    "userId",
    "packageId",
    "status",
    "message",
]

_EVENT_TYPE_RE = re.compile(r"[^a-z0-9._-]+")


def _stringify(value) -> str:
    """Port of stringifyFieldValue (Node String() semantics)."""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _normalize_event_type(event_type: str | None, title: str | None) -> str:
    """Port of normalizeEventType(options.eventType || options.title)."""
    raw = event_type or title or ""
    normalized = raw.strip().lower()
    normalized = _EVENT_TYPE_RE.sub("_", normalized)
    return normalized or "lark.alert"


def compute_dedupe_key(
    event_type: str | None,
    title: str | None,
    fields: dict | None = None,
    dedupe_parts: list | None = None,
) -> str:
    """Return ``alert:{eventType}:{sha1(fingerprint)}`` exactly like lark-alert.ts."""
    normalized_event_type = _normalize_event_type(event_type, title)

    if dedupe_parts:
        parts = [_stringify(p) for p in dedupe_parts if p not in (None, "")]
        fingerprint = "|".join(parts) if parts else (title or "")
    else:
        source = fields or {}
        parts = []
        for key in DEFAULT_FIELD_KEYS:
            value = source.get(key)
            if value not in (None, ""):
                parts.append(f"{key}:{_stringify(value)}")
        fingerprint = "|".join(parts) if parts else (title or "")

    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
    return f"alert:{normalized_event_type}:{digest}"
