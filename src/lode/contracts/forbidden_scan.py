"""Detect removed business contracts in selected source paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lode.contracts.checks import ROOT

DEFAULT_PATHS = (
    "src",
    "scripts",
    "tests",
    "alembic",
    "apps/web",
    "CLAUDE.md",
    "README.md",
)
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]


_REMOVED_SERVICE_BINDING = "Application" + "ServiceBinding"
_REMOVED_INVESTIGATION_SNAPSHOT = "Investigation" + "ServiceSnapshot"
_REMOVED_SERVICE_MODEL = "Ser" + "vice"
_REMOVED_SERVICE_ROUTE = "serv" + "ices"
_REMOVED_APPLICATION_ROUTE = "applica" + "tions"
_REMOVED_SERVICE_FIELD = "service" + "_name"
_REMOVED_REQUEST_FIELD = "request" + "_id"
_REMOVED_COMMIT_FIELD = "git" + "_commit"

RULES = (
    Rule(
        "removed_service_model",
        re.compile(
            rf"\b(?:{_REMOVED_SERVICE_BINDING}|{_REMOVED_INVESTIGATION_SNAPSHOT}|"
            rf"{_REMOVED_SERVICE_MODEL})\b"
        ),
    ),
    Rule(
        "removed_service_route",
        re.compile(
            rf"/(?:{_REMOVED_SERVICE_ROUTE}|workspaces/\{{[^}}]+\}}/"
            rf"{_REMOVED_SERVICE_ROUTE})(?:\b|/)"
        ),
    ),
    Rule(
        "removed_alert_field",
        re.compile(rf"\b(?:{_REMOVED_SERVICE_FIELD}|{_REMOVED_REQUEST_FIELD})\b"),
    ),
    Rule("removed_alert_revision", re.compile(rf"\b{_REMOVED_COMMIT_FIELD}\b")),
    Rule(
        "removed_application_resource",
        re.compile(rf"/(?:{_REMOVED_APPLICATION_ROUTE})(?:\b|/)"),
    ),
)

_VERSIONED_IMPLEMENTATION_NAME = re.compile(r"v[0-9]+", re.IGNORECASE)


def _iter_files(paths: list[Path]):
    for path in paths:
        if path.is_file():
            yield path
            continue
        if not path.exists():
            continue
        for candidate in sorted(path.rglob("*")):
            if not candidate.is_file() or candidate.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if any(
                part in {".git", ".next", "node_modules", "__pycache__"} for part in candidate.parts
            ):
                continue
            yield candidate


def scan(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in _iter_files(paths):
        if _VERSIONED_IMPLEMENTATION_NAME.search(path.name):
            location = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            findings.append(f"{location}:versioned_implementation_filename")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            for rule in RULES:
                if rule.pattern.search(line):
                    location = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                    findings.append(f"{location}:{line_number}:{rule.name}: {line.strip()}")
    return findings
