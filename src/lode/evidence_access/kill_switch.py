"""Hierarchical fail-closed authorization kill switches."""

from __future__ import annotations

from dataclasses import dataclass, field

from lode.evidence_access.types import AccessRejection


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
