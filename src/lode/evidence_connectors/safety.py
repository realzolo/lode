"""Default-on masking and prompt-injection marking for external evidence."""

from __future__ import annotations

import re
from typing import Any

from lode.evidence_connectors.types import ProviderExecutionError

_SECRET_PATTERNS = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+={0,2}")),
    (
        "connection_string",
        re.compile(r"(?i)(?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^\s'\"]+"),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{6,256}"
        ),
    ),
)
_PROMPT_INJECTION = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|"
    r"<\|(?:system|assistant|developer)\|>|do\s+not\s+follow\s+the\s+user)"
)


def sanitize_evidence(value: Any) -> tuple[Any, tuple[str, ...], bool]:
    categories: set[str] = set()
    injection = False
    nodes = 0

    def scrub(item: Any, depth: int) -> Any:
        nonlocal injection, nodes
        nodes += 1
        if nodes > 100_000 or depth > 64:
            raise ProviderExecutionError(
                "invalid_response", "normalized evidence exceeds structure budget"
            )
        if isinstance(item, dict):
            return {str(key): scrub(child, depth + 1) for key, child in item.items()}
        if isinstance(item, list):
            return [scrub(child, depth + 1) for child in item]
        if isinstance(item, str):
            if len(item.encode()) > 256 * 1024:
                raise ProviderExecutionError(
                    "invalid_response", "evidence string exceeds byte budget"
                )
            injection = injection or bool(_PROMPT_INJECTION.search(item))
            masked = item
            for name, pattern in _SECRET_PATTERNS:
                if pattern.search(masked):
                    categories.add(name)
                    masked = pattern.sub(f"<REDACTED:{name}>", masked)
            return masked
        if item is None or isinstance(item, (bool, int, float)):
            return item
        raise ProviderExecutionError(
            "invalid_response", "provider evidence contains unsupported values"
        )

    return scrub(value, 0), tuple(sorted(categories)), injection
