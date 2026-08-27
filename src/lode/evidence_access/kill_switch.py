"""Hierarchical fail-closed authorization kill switches."""

from __future__ import annotations

from dataclasses import dataclass, field

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
    return EvidenceKillSwitch(
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
