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
_REMOVED_INVESTIGATION_POLICY_MODEL = "Investigation" + "PolicyRevision"
_REMOVED_INVESTIGATION_POLICY_TABLE = "investigation" + "_policy_revisions"
_REMOVED_INVESTIGATION_POLICY_FIELD = "investigation" + "_policy_revision_id"
_REMOVED_EVIDENCE_STEP_LIMIT = "max" + "_evidence_steps"
_REMOVED_GIT_GRANT_MODEL = "WorkspaceGit" + "AccountGrant"
_REMOVED_GIT_ENTITLEMENT_MODEL = "WorkspaceGitRepository" + "Entitlement"
_REMOVED_GIT_GRANT_TABLE = "workspace_git" + "_account_grants"
_REMOVED_GIT_ENTITLEMENT_TABLE = "workspace_git_repository" + "_entitlements"
_REMOVED_GIT_ENTITLEMENT_FIELD = "repository" + "_entitlement_id"
_REMOVED_INVESTIGATION_POLICY_ROUTE = "investigation" + "-policy"
_REMOVED_REPOSITORY_CANDIDATES_ROUTE = "repository" + "-candidates"
_REMOVED_GIT_GRANTS_ROUTE = "git-account" + "-grants"

RULES = (
    Rule(
        "removed_service_model",
        re.compile(
            rf"\b(?:{_REMOVED_SERVICE_BINDING}|{_REMOVED_INVESTIGATION_SNAPSHOT})\b|"
            rf"\bclass\s+{_REMOVED_SERVICE_MODEL}\b|"
            rf"\b{_REMOVED_SERVICE_MODEL}\s*\(|"
            rf"\b(?:from\s+[\w.]+\s+import|import)\s+{_REMOVED_SERVICE_MODEL}\b"
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
    Rule(
        "removed_investigation_policy",
        re.compile(
            rf"\b(?:{_REMOVED_INVESTIGATION_POLICY_MODEL}|"
            rf"{_REMOVED_INVESTIGATION_POLICY_TABLE}|"
            rf"{_REMOVED_INVESTIGATION_POLICY_FIELD}|"
            rf"{_REMOVED_EVIDENCE_STEP_LIMIT})\b|"
            rf"/(?:{_REMOVED_INVESTIGATION_POLICY_ROUTE})(?:\b|/)"
        ),
    ),
    Rule(
        "removed_git_authorization_layer",
        re.compile(
            rf"\b(?:{_REMOVED_GIT_GRANT_MODEL}|{_REMOVED_GIT_ENTITLEMENT_MODEL}|"
            rf"{_REMOVED_GIT_GRANT_TABLE}|{_REMOVED_GIT_ENTITLEMENT_TABLE}|"
            rf"{_REMOVED_GIT_ENTITLEMENT_FIELD})\b|"
            rf"/(?:{_REMOVED_REPOSITORY_CANDIDATES_ROUTE}|{_REMOVED_GIT_GRANTS_ROUTE})(?:\b|/)"
        ),
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
