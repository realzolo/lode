"""Bounded, stack-first helpers for immutable Git source evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SKIP_DIRS = {
    ".git",
    ".next",
    ".nuxt",
    ".output",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
_SOURCE_EXT = {
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}
_TOKEN_SPLIT = re.compile(r"[\s:.,/()\[\]{}'\"`]+")
_STOPWORDS = {
    "and",
    "cause",
    "error",
    "exception",
    "failed",
    "failure",
    "false",
    "from",
    "none",
    "null",
    "occurred",
    "service",
    "stack",
    "that",
    "this",
    "timeout",
    "traceback",
    "true",
    "with",
}
_STACK_PATTERNS = (
    re.compile(
        r'File\s+"(?P<path>[^\"]+)",\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<symbol>[^\s]+))?'
    ),
    re.compile(
        r"(?:at\s+(?:(?P<symbol>[^\s(]+)\s+\()?)(?P<path>[^\s():]+):(?P<line>\d+)(?::\d+)?\)?"
    ),
    re.compile(
        r"(?P<path>[\w./\\-]+\.(?:go|rs|rb|php|cs|swift|scala|kt|kts)):(?P<line>\d+)(?::\d+)?"
    ),
)
_CALL_PATTERN = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")
_CALL_STOPWORDS = {
    "Boolean",
    "Error",
    "Number",
    "Object",
    "Promise",
    "Response",
    "String",
    "catch",
    "for",
    "if",
    "switch",
    "while",
}


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_walk_strings(item))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_walk_strings(item))
        return result
    return []


def _walk_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.extend([str(key), *_walk_keys(item)])
        return result
    if isinstance(value, (list, tuple)):
        return [key for item in value for key in _walk_keys(item)]
    return []


def derive_query_terms(incident_input: object) -> list[str]:
    """Derive bounded code identifiers from the complete normalized error."""
    raw: list[str] = []
    fields = getattr(incident_input, "fields", None) or {}
    properties = getattr(incident_input, "error_properties", None) or {}
    cause = getattr(incident_input, "error_cause", None)
    scope = getattr(incident_input, "scope", None) or {}
    contract_field_values = [
        value
        for key, value in fields.items()
        if isinstance(value, str)
        and any(label in str(key).lower() for label in ("code", "provider", "method", "error"))
    ]
    prioritized = [
        getattr(incident_input, "error_name", None),
        *contract_field_values,
        *_walk_strings(properties),
        *_walk_strings(cause),
        getattr(incident_input, "error_message", None),
        *_walk_strings(fields),
        *_walk_strings(scope),
        *_walk_keys(properties),
        *_walk_keys(fields),
    ]
    for value in prioritized:
        if not isinstance(value, str):
            continue
        for token in _TOKEN_SPLIT.split(value):
            token = token.strip()
            if len(token) >= 4 and not token.isdigit():
                raw.append(token)
    terms: list[str] = []
    seen: set[str] = set()
    for token in raw:
        normalized = token.lower()
        if normalized in seen or normalized in _STOPWORDS:
            continue
        seen.add(normalized)
        terms.append(token)
        if len(terms) == 24:
            break
    return terms


def extract_stack_frames(stack: str | None) -> list[dict[str, Any]]:
    """Parse source locators without treating arbitrary stack text as a path."""
    frames: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw_line in (stack or "").splitlines():
        for pattern in _STACK_PATTERNS:
            match = pattern.search(raw_line)
            if not match:
                continue
            groups = match.groupdict()
            path = str(groups.get("path") or "").replace("\\", "/").strip()
            if not path or "://" in path:
                continue
            line = int(groups["line"])
            key = (path, line)
            if key not in seen:
                frames.append(
                    {
                        "path": path,
                        "line": line,
                        "symbol": (groups.get("symbol") or "").strip() or None,
                        "raw": raw_line.strip()[:1_000],
                    }
                )
                seen.add(key)
            break
    return frames[:40]


def _resolve_frame_path(root: Path, raw_path: str) -> Path | None:
    normalized = raw_path.lstrip("./")
    direct = (root / normalized).resolve()
    if direct.is_relative_to(root.resolve()) and direct.is_file():
        return direct
    parts = Path(normalized).parts
    candidates: list[Path] = []
    for path in root.rglob(Path(normalized).name):
        if path.is_file() and not any(part in _SKIP_DIRS for part in path.parts):
            relative_parts = path.relative_to(root).parts
            suffix = parts[-min(len(parts), len(relative_parts)) :]
            if tuple(relative_parts[-len(suffix) :]) == tuple(suffix):
                candidates.append(path)
    return sorted(candidates, key=lambda item: len(item.parts))[0] if candidates else None


def _symbol_range(
    lines: list[str], target_line: int, symbol_hint: str | None
) -> tuple[int, int, str | None]:
    target = max(1, min(target_line, len(lines)))
    declarations = (
        re.compile(r"^\s*(?:async\s+)?(?:def|function|func|fn)\s+([\w$]+)"),
        re.compile(r"^\s*export\s+default\s+([\w$]+)"),
        re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>"
        ),
        re.compile(r"^\s*(?:export\s+)?class\s+([\w$]+)"),
        re.compile(
            r"^\s*(?:public|private|protected|static|final|synchronized|override|open|internal|export|async|[\w<>\[\],?]+\s+)*\s+([\w$]+)\s*\([^;]*\)\s*(?:\{|:)\s*$"
        ),
    )

    def declared_symbol(line: str) -> str | None:
        for declaration in declarations:
            match = declaration.match(line)
            if match:
                symbol = match.group(1)
                if symbol not in {
                    "await",
                    "catch",
                    "else",
                    "for",
                    "if",
                    "return",
                    "switch",
                    "while",
                }:
                    return symbol
        return None

    start = max(1, target - 24)
    symbol = symbol_hint
    declaration_index: int | None = None
    for index in range(target - 1, -1, -1):
        declared = declared_symbol(lines[index])
        if declared:
            declaration_index = index
            symbol = symbol or declared
            break
    branch = re.compile(r"^\s*(?:if|else\s+if|else|catch|switch|case)\b.*\{")
    branch_index = next(
        (
            index
            for index in range(target - 1, max(-1, target - 80), -1)
            if branch.match(lines[index])
        ),
        None,
    )
    if branch_index is not None:
        start = branch_index + 1
        depth = 0
        for index in range(branch_index, min(len(lines), branch_index + 200)):
            depth += lines[index].count("{") - lines[index].count("}")
            if index + 1 >= target and depth <= 0:
                return start, index + 1, symbol
    elif declaration_index is not None and target - declaration_index <= 120:
        start = declaration_index + 1
    end = min(len(lines), max(target + 24, start + 12))
    base_indent = len(lines[start - 1]) - len(lines[start - 1].lstrip()) if lines else 0
    for index in range(target, min(len(lines), start + 160)):
        stripped = lines[index].strip()
        indent = len(lines[index]) - len(lines[index].lstrip())
        if (
            stripped
            and index + 1 > target
            and indent <= base_indent
            and declared_symbol(lines[index])
        ):
            end = index
            break
    return start, end, symbol


def stack_hits(
    root: Path, stack: str | None, *, max_files: int = 12, max_bytes: int = 160_000
) -> list[dict[str, Any]]:
    """Open exact stack locations and archive the surrounding symbol body."""
    hits: list[dict[str, Any]] = []
    used = 0
    seen: set[tuple[str, int]] = set()
    for frame in extract_stack_frames(stack):
        path = _resolve_frame_path(root, frame["path"])
        if path is None or path.suffix.lower() not in _SOURCE_EXT:
            continue
        relative = str(path.relative_to(root))
        key = (relative, frame["line"])
        if key in seen:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines or frame["line"] > len(lines):
            continue
        start, end, symbol = _symbol_range(lines, frame["line"], frame.get("symbol"))
        snippet = "\n".join(lines[start - 1 : end])
        size = len(snippet.encode("utf-8"))
        if hits and used + size > max_bytes:
            continue
        hits.append(
            {
                "path": relative,
                "line": frame["line"],
                "snippet_start_line": start,
                "snippet_end_line": end,
                "snippet": snippet,
                "symbol": symbol,
                "terms": [],
                "score": 10_000 - len(hits),
                "selection_reason": "exact incident stack frame",
                "stack_frame": frame["raw"],
            }
        )
        seen.add(key)
        used += size
        if len(hits) == max_files:
            break
    return hits


def path_hits(
    root: Path,
    path_hints: list[str],
    *,
    max_files: int = 12,
    max_bytes: int = 160_000,
) -> list[dict[str, Any]]:
    """Open exact, evidence-grounded repository paths without executing code."""
    hits: list[dict[str, Any]] = []
    used = 0
    seen: set[str] = set()
    for hint in path_hints:
        path = _resolve_frame_path(root, hint)
        if path is None or path.suffix.lower() not in _SOURCE_EXT:
            continue
        relative = str(path.relative_to(root))
        if relative in seen:
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines:
            continue
        start, end, symbol = _symbol_range(lines, 1, None)
        snippet = "\n".join(lines[start - 1 : end])
        size = len(snippet.encode("utf-8"))
        if hits and used + size > max_bytes:
            continue
        hits.append(
            {
                "path": relative,
                "line": 1,
                "snippet_start_line": start,
                "snippet_end_line": end,
                "snippet": snippet,
                "symbol": symbol,
                "terms": [],
                "score": 9_000 - len(hits),
                "selection_reason": "exact evidence-grounded path hint",
            }
        )
        seen.add(relative)
        used += size
        if len(hits) == max_files:
            break
    return hits


def search_tree(
    root: Path,
    terms: list[str],
    *,
    max_files: int = 20,
    max_bytes: int = 200_000,
    snippet_lines: int = 48,
) -> list[dict[str, Any]]:
    """Return bounded lexical candidates; these are never causal proof alone."""
    if not terms:
        return []
    patterns = [(term, re.compile(re.escape(term), re.IGNORECASE)) for term in terms]
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in _SOURCE_EXT
            or any(part in _SKIP_DIRS for part in path.parts)
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        matches = [(term, pattern.search(text)) for term, pattern in patterns]
        matches = [(term, match) for term, match in matches if match]
        if not matches:
            continue
        # Terms are already ordered by causal specificity. Center the snippet on
        # the first matching term instead of an earlier generic word elsewhere.
        line = text.count("\n", 0, matches[0][1].start()) + 1
        lines = text.splitlines()
        start, end, symbol = _symbol_range(lines, line, None)
        if end - start + 1 > snippet_lines:
            start = max(1, line - snippet_lines // 2)
            end = min(len(lines), start + snippet_lines - 1)
        snippet = "\n".join(lines[start - 1 : end])
        candidates.append(
            {
                "path": str(path.relative_to(root)),
                "line": line,
                "snippet_start_line": start,
                "snippet_end_line": end,
                "snippet": snippet,
                "symbol": symbol,
                "terms": [term for term, _ in matches],
                "score": len(matches) * 100
                + (50 if any(term.isupper() for term, _ in matches) else 0),
                "selection_reason": "error identifier or symbol candidate; not causal proof",
            }
        )
    hits: list[dict[str, Any]] = []
    used = 0
    for candidate in sorted(candidates, key=lambda item: (-item["score"], item["path"])):
        size = len(candidate["snippet"].encode("utf-8"))
        if hits and used + size > max_bytes:
            continue
        hits.append(candidate)
        used += size
        if len(hits) == max_files:
            break
    return hits


def related_symbol_hits(
    root: Path,
    primary_hits: list[dict[str, Any]],
    *,
    max_files: int = 6,
    max_bytes: int = 100_000,
) -> list[dict[str, Any]]:
    """Open definitions of functions called by the selected error branches."""

    symbols: list[str] = []
    for hit in primary_hits:
        for symbol in _CALL_PATTERN.findall(str(hit.get("snippet") or "")):
            if symbol in _CALL_STOPWORDS or symbol in symbols or len(symbol) < 4:
                continue
            symbols.append(symbol)
            if len(symbols) == 16:
                break
    if not symbols:
        return []

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in _SOURCE_EXT
            or any(part in _SKIP_DIRS for part in path.parts)
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for priority, symbol in enumerate(symbols):
            patterns = (
                re.compile(
                    rf"^\s*(?:export\s+)?(?:async\s+)?(?:function|def|func|fn)\s+{re.escape(symbol)}\b",
                    re.MULTILINE,
                ),
                re.compile(
                    rf"^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(symbol)}\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>",
                    re.MULTILINE,
                ),
            )
            match = next(
                (candidate for pattern in patterns if (candidate := pattern.search(text))), None
            )
            relative = str(path.relative_to(root))
            if match is None or (relative, symbol) in seen:
                continue
            lines = text.splitlines()
            line = text.count("\n", 0, match.start()) + 1
            start, end, identified = _symbol_range(lines, line, symbol)
            snippet = "\n".join(lines[start - 1 : end])
            candidates.append(
                {
                    "path": relative,
                    "line": line,
                    "snippet_start_line": start,
                    "snippet_end_line": end,
                    "snippet": snippet,
                    "symbol": identified or symbol,
                    "terms": [symbol],
                    "score": 1_000 - priority,
                    "selection_reason": "called symbol definition candidate; not causal proof",
                    "related_symbol": symbol,
                }
            )
            seen.add((relative, symbol))

    hits: list[dict[str, Any]] = []
    used = 0
    for candidate in sorted(candidates, key=lambda item: (-item["score"], item["path"])):
        size = len(candidate["snippet"].encode())
        if hits and used + size > max_bytes:
            continue
        hits.append(candidate)
        used += size
        if len(hits) == max_files:
            break
    return hits
