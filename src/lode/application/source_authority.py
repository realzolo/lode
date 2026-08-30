"""Deterministic source revision and runtime configuration authority rules."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lode.domain.model_execution import (
    ConfigurationAuthorityAssessment,
    SourceAuthorityAssessment,
    SourceRevisionOrigin,
)


class SourceAuthorityEngine:
    def assess(
        self,
        *,
        repository_snapshot_id: int,
        revision_origin: SourceRevisionOrigin,
        requested_ref: str | None,
        resolved_sha: str | None,
        incident_source_revision: str | None,
        runtime_revision_evidence_refs: Sequence[int] = (),
        contradiction_evidence_refs: Sequence[int] = (),
        contradiction_reasons: Sequence[str] = (),
    ) -> SourceAuthorityAssessment:
        runtime_refs = tuple(runtime_revision_evidence_refs)
        contradiction_refs = tuple(contradiction_evidence_refs)
        reasons: list[str] = []
        if resolved_sha is None:
            authority_status = "unavailable"
            compatibility_status = "not_checked"
            reasons.append("source_revision_unavailable")
        elif contradiction_refs:
            authority_status = "contradicted"
            compatibility_status = "incompatible"
            reasons.extend(contradiction_reasons or ("runtime_revision_contradicted",))
        elif (
            revision_origin == "alert_revision" and incident_source_revision == resolved_sha
        ) or revision_origin == "bound_branch_head":
            authority_status = "authoritative"
            compatibility_status = "not_checked"
        elif revision_origin == "runtime_observed" and runtime_refs:
            authority_status = "corroborated"
            compatibility_status = "compatible"
        else:
            authority_status = "contradicted"
            compatibility_status = "incompatible"
            if revision_origin == "alert_revision":
                reasons.append("resolved_revision_does_not_match_alert_source")
            else:
                reasons.append("runtime_revision_has_no_independent_evidence")
        return SourceAuthorityAssessment(
            repository_snapshot_id=repository_snapshot_id,
            revision_origin=revision_origin,
            requested_ref=requested_ref,
            resolved_sha=resolved_sha,
            authority_status=authority_status,
            compatibility_status=compatibility_status,
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
