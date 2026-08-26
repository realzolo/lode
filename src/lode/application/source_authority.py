"""Deterministic source revision and runtime configuration authority rules."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lode.domain.model_execution import (
    AuthorityStatus,
    ConfigurationAuthorityAssessment,
    SourceAuthorityAssessment,
    SourceRevisionRole,
)


class SourceAuthorityEngine:
    def assess(
        self,
        *,
        repository_snapshot_id: int,
        revision_role: SourceRevisionRole,
        requested_ref: str | None,
        resolved_sha: str | None,
        incident_source_revision: str | None,
        frozen_resolution_status: AuthorityStatus | None = None,
        runtime_revision_evidence_refs: Sequence[int] = (),
        contradiction_evidence_refs: Sequence[int] = (),
    ) -> SourceAuthorityAssessment:
        runtime_refs = tuple(runtime_revision_evidence_refs)
        contradiction_refs = tuple(contradiction_evidence_refs)
        reasons: list[str] = []
        if resolved_sha is None:
            status = "unresolved"
            reasons.append("source_revision_unresolved")
        elif contradiction_refs:
            status = "contradicted"
            reasons.append("runtime_revision_contradicted")
        elif frozen_resolution_status == "unverified" and revision_role == "incident_source":
            status = "unverified"
            reasons.append("ambiguous_source_revision")
        elif revision_role == "incident_source" and incident_source_revision == resolved_sha:
            status = "exact"
        elif revision_role == "runtime_identified" and runtime_refs:
            status = "corroborated"
        else:
            status = "unverified"
            if revision_role == "repository_search_candidate":
                reasons.append("repository_search_is_not_runtime_evidence")
            elif revision_role == "incident_source":
                reasons.append("resolved_revision_does_not_match_alert_source")
            else:
                reasons.append("runtime_revision_has_no_independent_evidence")
        return SourceAuthorityAssessment(
            repository_snapshot_id=repository_snapshot_id,
            revision_role=revision_role,
            requested_ref=requested_ref,
            resolved_sha=resolved_sha,
            status=status,
            runtime_evidence_refs=tuple(dict.fromkeys((*runtime_refs, *contradiction_refs))),
            mismatch_reasons=tuple(reasons),
        )


class ConfigurationAuthorityEngine:
    def assess(
        self,
        *,
        scope: str,
        declared_value: Any,
        runtime_value: Any,
        runtime_evidence_refs: Sequence[int] = (),
    ) -> ConfigurationAuthorityAssessment:
        refs = tuple(runtime_evidence_refs)
        if not refs:
            status = "unknown"
        elif declared_value == runtime_value:
            status = "corroborated"
        else:
            status = "contradicted"
        return ConfigurationAuthorityAssessment(
            scope=scope,
            declared_value=declared_value,
            runtime_value=runtime_value,
            status=status,
            evidence_refs=refs,
        )
