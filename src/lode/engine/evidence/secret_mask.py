"""Default-on secret masking for evidence excerpts.

Source snippets and diffs can contain credentials, tokens, or private keys.
Every excerpt that leaves the gateway is passed through :func:`mask_secrets`
before it is persisted, so an ``EvidenceArtifact.redacted_excerpt`` never holds
a live secret. Matching is conservative and pattern-based; when in doubt we
redact (fail closed), which is the correct posture for incident evidence that
may later be exported or shown in a UI.
"""

from __future__ import annotations

import re

# Order is significant: the more specific patterns run first so a value already
# replaced by an earlier rule is not double-counted.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "aws_secret_key",
        re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"),
    ),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    (
        "bearer_token",
        re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+={0,2}"),
    ),
    (
        "connection_string",
        re.compile(r"(?i)(?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^\s'\"]+"),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{6,64}"
        ),
    ),
]


def mask_secrets(text: str) -> tuple[str, list[str]]:
    """Return ``(masked_text, [categories_found])``.

    Every recognised secret shape is replaced with ``<REDACTED:category>``. The
    returned category list is de-duplicated and sorted for stable audit logs.
    """
    if not text:
        return text, []
    found: set[str] = set()
    masked = text
    for name, pattern in _SECRET_PATTERNS:

        def _replace(match: re.Match[str], _name: str = name) -> str:
            found.add(_name)
            return f"<REDACTED:{_name}>"

        masked = pattern.sub(_replace, masked)
    return masked, sorted(found)
