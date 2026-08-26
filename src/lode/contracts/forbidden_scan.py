"""Detect removed V1 business contracts in selected source paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lode.contracts.checks import ROOT


DEFAULT_PATHS = ("src", "tests", "alembic", "apps/web", "CLAUDE.md", "README.md")
TEXT_SUFFIXES = {
    ".css", ".html", ".js", ".json", ".md", ".mjs", ".py", ".sh",
    ".toml", ".ts", ".tsx", ".yaml", ".yml",
}


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]


RULES = (
    Rule("removed_service_model", re.compile(r"\b(?:ApplicationServiceBinding|InvestigationServiceSnapshot|Service)\b")),
    Rule("removed_service_route", re.compile(r"/(?:services|workspaces/\{[^}]+\}/services)(?:\b|/)")),
    Rule("removed_alert_field", re.compile(r"\b(?:service_name|request_id)\b")),
    Rule("removed_alert_revision", re.compile(r"\bgit_commit\b")),
    Rule("removed_application_resource", re.compile(r"/(?:applications)(?:\b|/)")),
)


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
            if any(part in {".git", ".next", "node_modules", "__pycache__"} for part in candidate.parts):
                continue
            yield candidate


def scan(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in _iter_files(paths):
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
