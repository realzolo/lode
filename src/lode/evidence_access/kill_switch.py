"""Hierarchical fail-closed authorization kill switches."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lode.config import settings
from lode.evidence_access.types import AccessRejection

_LANGUAGES = {
    "logql",
    "elasticsearch_query_dsl",
    "opensearch_query_dsl",
    "sql",
    "https",
    "command",
}


@dataclass(slots=True)
class EvidenceKillSwitch:
    globally_enabled: bool = True
    disabled_workspaces: set[int] = field(default_factory=set)
    disabled_connectors: set[int] = field(default_factory=set)
    disabled_languages: set[str] = field(default_factory=set)
    runner_enabled: bool = True

    def check(self, *, workspace_id: int, connector_id: int, language: str) -> None:
        reason = None
        if not self.globally_enabled:
            reason = "global"
        elif workspace_id in self.disabled_workspaces:
            reason = "workspace"
        elif connector_id in self.disabled_connectors:
            reason = "connector"
        elif language in self.disabled_languages:
            reason = "language"
        elif language == "command" and not self.runner_enabled:
            reason = "runner"
        if reason is not None:
            raise AccessRejection(
                "scope_violation",
                "native evidence authorization is disabled",
                {"kill_switch": reason},
            )


def configured_kill_switch() -> EvidenceKillSwitch:
    languages = _csv_values(settings.evidence_disabled_languages)
    unknown = languages - _LANGUAGES
    if unknown:
        raise RuntimeError(f"unknown disabled evidence languages: {sorted(unknown)}")
    configured = EvidenceKillSwitch(
        globally_enabled=settings.evidence_access_enabled,
        disabled_workspaces=_positive_ids(
            settings.evidence_disabled_workspace_ids, "LODE_EVIDENCE_DISABLED_WORKSPACE_IDS"
        ),
        disabled_connectors=_positive_ids(
            settings.evidence_disabled_connector_ids, "LODE_EVIDENCE_DISABLED_CONNECTOR_IDS"
        ),
        disabled_languages=languages,
        runner_enabled=settings.command_runner_enabled,
    )
    if not settings.evidence_kill_switch_file:
        return configured
    runtime = _runtime_file(settings.evidence_kill_switch_file)
    return EvidenceKillSwitch(
        globally_enabled=configured.globally_enabled and runtime.globally_enabled,
        disabled_workspaces=configured.disabled_workspaces | runtime.disabled_workspaces,
        disabled_connectors=configured.disabled_connectors | runtime.disabled_connectors,
        disabled_languages=configured.disabled_languages | runtime.disabled_languages,
        runner_enabled=configured.runner_enabled and runtime.runner_enabled,
    )


def _csv_values(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _positive_ids(value: str, name: str) -> set[int]:
    try:
        result = {int(item) for item in _csv_values(value)}
    except ValueError as exc:
        raise RuntimeError(f"{name} must contain comma-separated positive integers") from exc
    if any(item < 1 for item in result):
        raise RuntimeError(f"{name} must contain comma-separated positive integers")
    return result


def _runtime_file(value: str) -> EvidenceKillSwitch:
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError("LODE_EVIDENCE_KILL_SWITCH_FILE must be an absolute path")
    try:
        if path.stat().st_size > 16_384:
            raise RuntimeError("evidence kill switch file is too large")
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("evidence kill switch file is unavailable or invalid") from exc
    required = {
        "enabled",
        "disabled_workspace_ids",
        "disabled_connector_ids",
        "disabled_languages",
        "runner_enabled",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise RuntimeError("evidence kill switch file must contain the exact fields")
    enabled = _file_bool(document, "enabled")
    runner_enabled = _file_bool(document, "runner_enabled")
    workspaces = _file_ids(document, "disabled_workspace_ids")
    connectors = _file_ids(document, "disabled_connector_ids")
    languages = _file_languages(document)
    return EvidenceKillSwitch(
        globally_enabled=enabled,
        disabled_workspaces=workspaces,
        disabled_connectors=connectors,
        disabled_languages=languages,
        runner_enabled=runner_enabled,
    )


def _file_bool(document: dict[str, Any], name: str) -> bool:
    value = document[name]
    if not isinstance(value, bool):
        raise RuntimeError(f"evidence kill switch {name} must be a boolean")
    return value


def _file_ids(document: dict[str, Any], name: str) -> set[int]:
    value = document[name]
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value
    ):
        raise RuntimeError(f"evidence kill switch {name} must contain positive integers")
    if len(value) != len(set(value)):
        raise RuntimeError(f"evidence kill switch {name} must not contain duplicates")
    return set(value)


def _file_languages(document: dict[str, Any]) -> set[str]:
    value = document["disabled_languages"]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError("evidence kill switch disabled_languages must contain strings")
    languages = set(value)
    if len(value) != len(languages) or languages - _LANGUAGES:
        raise RuntimeError("evidence kill switch disabled_languages is invalid")
    return languages
