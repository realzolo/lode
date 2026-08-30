"""Structured secret masking for data that may enter persistent audit surfaces."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "aws_secret_key",
        re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"),
    ),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+={0,2}")),
    (
        "connection_string",
        re.compile(r"(?i)(?:postgres|postgresql|mysql|clickhouse|mongodb|redis|amqp)://[^\s'\"]+"),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
            r"\s*[:=]\s*['\"]?[^\s'\"]{6,64}"
        ),
    ),
)


def mask_text(value: str) -> tuple[str, tuple[str, ...]]:
    found: set[str] = set()
    masked = value
    for category, pattern in _SECRET_PATTERNS:
        def replace(_match: re.Match[str], name: str = category) -> str:
            found.add(name)
            return f"<REDACTED:{name}>"

        masked = pattern.sub(replace, masked)
    return masked, tuple(sorted(found))


def mask_structure(value: Any) -> tuple[Any, tuple[str, ...]]:
    """Mask every string leaf while preserving JSON-compatible structure."""

    found: set[str] = set()

    def visit(item: Any) -> Any:
        if isinstance(item, str):
            masked, categories = mask_text(item)
            found.update(categories)
            return masked
        if isinstance(item, Mapping):
            return {str(key): visit(child) for key, child in item.items()}
        if isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)):
            return [visit(child) for child in item]
        return item

    return visit(value), tuple(sorted(found))
