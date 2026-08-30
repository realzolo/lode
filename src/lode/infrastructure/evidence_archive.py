"""Archive normalized native-read results and server-owned correlation claims."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models import (
    AuthorizedEvidenceRead,
    EvidenceAccessDecision,
    EvidenceArtifact,
    EvidenceAssertion,
    EvidenceCollection,
    EvidenceLink,
    GitRepository,
    Investigation,
    InvestigationConnectorSnapshot,
    InvestigationRepositorySnapshot,
    NativeReadCandidate,
    ObservedEvent,
)
from lode.domain.investigation import canonical_hash
from lode.evidence_access.vault import EvidenceValueVault
from lode.masking import mask_structure

_BUSINESS_KEYS = (
    "request_id",
    "order_id",
    "event",
    "event_name",
    "result_code",
    "status_code",
    "pm_id",
    "method",
    "url",
    "service",
    "service_name",
    "app",
)
_KEY_VALUE = {
    key: re.compile(rf"(?i)(?:\"?{re.escape(key)}\"?)\s*[:=]\s*\"?([^\",\s}}]+)")
    for key in _BUSINESS_KEYS
}


class PostgresEvidenceResultArchiver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def archive(
        self,
        authorized: AuthorizedEvidenceRead,
        decision: EvidenceAccessDecision,
        result: Mapping[str, Any],
    ) -> tuple[int, ...]:
        candidate = await self.session.get(NativeReadCandidate, decision.candidate_id)
        investigation = await self.session.get(Investigation, authorized.investigation_id)
        snapshot = (
            await self.session.get(InvestigationConnectorSnapshot, candidate.connector_snapshot_id)
            if candidate is not None
            else None
        )
        if (
            candidate is None
            or investigation is None
            or snapshot is None
            or candidate.investigation_id != authorized.investigation_id
        ):
            raise ValueError("native read ownership failed during archive")

        trace_discovery = _is_trace_discovery(candidate, decision)
        uses_sealed_trace = _uses_sealed_trace(candidate)
        trace_value_ref = "incident.trace_id" if uses_sealed_trace else None
        redacted_result: Any = result
        if trace_value_ref is not None:
            sealed = await EvidenceValueVault(self.session).resolve(
                workspace_id=investigation.workspace_id,
                investigation_id=investigation.id,
                value_refs=(trace_value_ref,),
            )
            redacted_result = _replace_sealed_value(
                result,
                sealed[trace_value_ref],
                f"<SEALED:{trace_value_ref}>",
            )
        masked, categories = mask_structure(redacted_result)
        if not isinstance(masked, dict):
            raise TypeError("normalized evidence result must be an object")
        result_bytes = len(json.dumps(masked, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        collection = EvidenceCollection(
            investigation_id=authorized.investigation_id,
            operation_id=candidate.operation_id,
            connector_snapshot_id=candidate.connector_snapshot_id,
            collection_kind="native_read",
            status="succeeded",
            fingerprint=authorized.fingerprint,
            purpose=candidate.purpose,
            selector_masked={
                "language": candidate.language,
                "payload": candidate.payload_masked,
                "candidate_hash": candidate.candidate_hash,
                "trace_discovery": trace_discovery,
            },
            artifact_count=1,
            result_bytes=result_bytes,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        self.session.add(collection)
        await self.session.flush()
        artifact = EvidenceArtifact(
            investigation_id=authorized.investigation_id,
            collection_id=collection.id,
            artifact_kind=_artifact_kind(candidate.language),
            evidence_class="runtime",
            content_masked=masked,
            content_hash=canonical_hash(masked),
            provenance={
                "authorized_read_id": authorized.id,
                "access_decision_id": decision.id,
                "candidate_id": candidate.id,
                "candidate_hash": candidate.candidate_hash,
                "connector_snapshot_id": snapshot.id,
                "scope_coverage": _scope_coverage(snapshot, masked),
                "masking_categories": list(categories),
            },
            source_time_start=None,
            source_time_end=None,
            source_revision=None,
            data_class="masked",
            prompt_injection_markers=(
                [{"detected": True}] if result.get("prompt_injection_detected") is True else []
            ),
        )
        self.session.add(artifact)
        await self.session.flush()

        if candidate.language == "logql":
            await self._archive_runtime_revision_assertions(
                investigation_id=investigation.id,
                artifact=artifact,
                result=masked,
            )

        assertion = None
        if uses_sealed_trace:
            assertion = await self._archive_trace_assertion(
                candidate=candidate,
                decision=decision,
                snapshot=snapshot,
                artifact=artifact,
                result=masked,
                full_scope_discovery=trace_discovery,
            )
            artifact.provenance = {
                **artifact.provenance,
                "server_assertion_refs": [assertion.id],
                "sealed_correlation": {
                    "value_ref": trace_value_ref,
                    "match_mode": "exact_line_filter",
                    "server_generated": True,
                },
            }
        if candidate.language == "logql":
            await self._archive_loki_events(
                investigation_id=investigation.id,
                snapshot=snapshot,
                artifact=artifact,
                result=masked,
                assertion=assertion,
            )
        return (artifact.id,)

    async def _archive_runtime_revision_assertions(
        self,
        *,
        investigation_id: int,
        artifact: EvidenceArtifact,
        result: Mapping[str, Any],
    ) -> None:
        records = result.get("records")
        if not isinstance(records, list):
            return
        repositories = tuple(
            (
                await self.session.execute(
                    select(InvestigationRepositorySnapshot, GitRepository)
                    .join(
                        GitRepository,
                        GitRepository.id == InvestigationRepositorySnapshot.repository_id,
                    )
                    .where(InvestigationRepositorySnapshot.investigation_id == investigation_id)
                )
            ).all()
        )
        observations: set[tuple[int, str]] = set()
        for record in records:
            if not isinstance(record, Mapping):
                continue
            labels = record.get("labels")
            if not isinstance(labels, Mapping):
                continue
            identities = [
                value
                for key in ("app", "service", "service_name")
                if isinstance((value := labels.get(key)), str) and value
            ]
            for hint in _revision_hints(labels):
                for snapshot, repository in repositories:
                    if any(
                        _identity_matches_repository(identity, repository)
                        for identity in identities
                    ):
                        observations.add((snapshot.id, hint["sha"]))
        for repository_snapshot_id, runtime_sha in sorted(observations):
            snapshot = await self.session.get(
                InvestigationRepositorySnapshot, repository_snapshot_id
            )
            if snapshot is None:
                continue
            claim = {
                "claim_type": "runtime_revision_observed",
                "server_generated": True,
                "repository_snapshot_id": repository_snapshot_id,
                "runtime_observed_sha": runtime_sha,
                "frozen_revision_sha": snapshot.frozen_revision_sha,
                "compatibility_status": (
                    "incompatible"
                    if snapshot.frozen_revision_sha is not None
                    and snapshot.frozen_revision_sha != runtime_sha
                    else "not_checked"
                ),
            }
            assertion_hash = canonical_hash(
                {
                    "investigation_id": investigation_id,
                    "artifact_id": artifact.id,
                    "claim": claim,
                }
            )
            exists = (
                await self.session.execute(
                    select(EvidenceAssertion.id).where(
                        EvidenceAssertion.investigation_id == investigation_id,
                        EvidenceAssertion.assertion_hash == assertion_hash,
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue
            self.session.add(
                EvidenceAssertion(
                    investigation_id=investigation_id,
                    assertion_kind="fact",
                    status="confirmed",
                    statement="Runtime logs explicitly identified a repository revision.",
                    structured_claim=claim,
                    supporting_evidence_refs=[artifact.id],
                    counter_evidence_refs=[],
                    missing_validation=[],
                    assertion_hash=assertion_hash,
                )
            )

    async def _archive_trace_assertion(
        self,
        *,
        candidate: NativeReadCandidate,
        decision: EvidenceAccessDecision,
        snapshot: InvestigationConnectorSnapshot,
        artifact: EvidenceArtifact,
        result: Mapping[str, Any],
        full_scope_discovery: bool,
    ) -> EvidenceAssertion:
        coverage = _scope_coverage(snapshot, result)
        claim = _trace_correlation_claim(
            connector_snapshot_id=snapshot.id,
            candidate_id=candidate.id,
            access_decision_id=decision.id,
            full_scope_discovery=full_scope_discovery,
            coverage=coverage,
        )
        assertion_hash = canonical_hash(
            {
                "investigation_id": candidate.investigation_id,
                "artifact_id": artifact.id,
                "claim": claim,
            }
        )
        assertion = EvidenceAssertion(
            investigation_id=candidate.investigation_id,
            assertion_kind="fact",
            status="confirmed",
            statement=(
                "Loki returned these records from an authorized "
                f"{'full-scope' if full_scope_discovery else 'component-scoped'} query "
                "using the sealed incident trace as an exact line filter."
            ),
            structured_claim=claim,
            supporting_evidence_refs=[artifact.id],
            counter_evidence_refs=[],
            missing_validation=[],
            assertion_hash=assertion_hash,
        )
        self.session.add(assertion)
        await self.session.flush()
        self.session.add(
            EvidenceLink(
                investigation_id=candidate.investigation_id,
                source_type="assertion",
                source_id=assertion.id,
                artifact_id=artifact.id,
                relation="validates",
            )
        )
        return assertion

    async def _archive_loki_events(
        self,
        *,
        investigation_id: int,
        snapshot: InvestigationConnectorSnapshot,
        artifact: EvidenceArtifact,
        result: Mapping[str, Any],
        assertion: EvidenceAssertion | None,
    ) -> None:
        records = result.get("records")
        if not isinstance(records, list):
            return
        repositories = tuple(
            (
                await self.session.execute(
                    select(InvestigationRepositorySnapshot, GitRepository)
                    .join(
                        GitRepository,
                        GitRepository.id == InvestigationRepositorySnapshot.repository_id,
                    )
                    .where(InvestigationRepositorySnapshot.investigation_id == investigation_id)
                )
            ).all()
        )
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                continue
            labels = record.get("labels")
            labels = dict(labels) if isinstance(labels, Mapping) else {}
            value = record.get("value")
            if not isinstance(value, str):
                continue
            attributes = _event_attributes(value)
            identities = tuple(
                dict.fromkeys(
                    str(item)
                    for item in (
                        labels.get("app"),
                        labels.get("service"),
                        labels.get("service_name"),
                        attributes.get("app"),
                        attributes.get("service"),
                        attributes.get("service_name"),
                    )
                    if isinstance(item, str) and item
                )
            )
            component_candidates = [
                {
                    "identity": identity,
                    "location": "loki.labels_or_event",
                    "repository_snapshot_ids": [
                        repository_snapshot.id
                        for repository_snapshot, repository in repositories
                        if _identity_matches_repository(identity, repository)
                    ],
                }
                for identity in identities
            ]
            revision_hints = _revision_hints(labels)
            provider_position = (
                f"{record.get('timestamp', 'unknown')}:"
                f"{canonical_hash({'labels': labels, 'value': value})[:24]}"
            )
            existing = (
                await self.session.execute(
                    select(ObservedEvent.id).where(
                        ObservedEvent.investigation_id == investigation_id,
                        ObservedEvent.connector_snapshot_id == snapshot.id,
                        ObservedEvent.provider_position == provider_position,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            self.session.add(
                ObservedEvent(
                    investigation_id=investigation_id,
                    occurred_at=_loki_timestamp(record.get("timestamp")),
                    connector_snapshot_id=snapshot.id,
                    provider_position=provider_position,
                    raw_excerpt_masked=value[:40_000],
                    attributes_masked=attributes,
                    resource_attributes_masked=labels,
                    trace_match=(
                        {
                            "assertion_id": assertion.id,
                            "value_ref": "incident.trace_id",
                            "location": "server_generated_exact_line_filter",
                        }
                        if assertion is not None
                        else {}
                    ),
                    component_candidates=component_candidates,
                    relation_hints=[],
                    revision_hints=revision_hints,
                    provider_metadata={
                        "loki.result_type": result.get("result_type"),
                        "loki.record_index": index,
                        "loki.truncated": bool(result.get("truncated")),
                    },
                    evidence_artifact_id=artifact.id,
                )
            )


def _is_trace_discovery(candidate: NativeReadCandidate, decision: EvidenceAccessDecision) -> bool:
    root_filter = decision.constraint_diff.get("root_filter")
    return (
        candidate.language == "logql"
        and "incident.trace_id" in candidate.value_bindings.values()
        and isinstance(root_filter, Mapping)
        and root_filter.get("full_scope_discovery") is True
        and any(
            isinstance(item, Mapping)
            and item.get("check") == "sealed_trace_global_discovery"
            and item.get("outcome") == "allow"
            for item in decision.validation_decisions
        )
    )


def _uses_sealed_trace(candidate: NativeReadCandidate) -> bool:
    return (
        candidate.language == "logql" and "incident.trace_id" in candidate.value_bindings.values()
    )


def _replace_sealed_value(value: Any, secret: str, replacement: str) -> Any:
    if isinstance(value, str):
        return value.replace(secret, replacement)
    if isinstance(value, Mapping):
        return {
            str(key): _replace_sealed_value(child, secret, replacement)
            for key, child in value.items()
        }
    if isinstance(value, list | tuple):
        return [_replace_sealed_value(child, secret, replacement) for child in value]
    return value


def _scope_coverage(
    snapshot: InvestigationConnectorSnapshot, result: Mapping[str, Any]
) -> dict[str, Any]:
    branches = snapshot.scope_config.get("root_filter_dnf")
    branches = branches if isinstance(branches, list) else []
    allowed_apps = sorted(
        {
            app
            for branch in branches
            if isinstance(branch, list)
            for condition in branch
            if isinstance(condition, Mapping) and condition.get("label") == "app"
            for app in condition.get("values", [])
            if isinstance(app, str)
        }
    )
    records = result.get("records")
    records = records if isinstance(records, list) else []
    returned_apps = sorted(
        {
            str(labels["app"])
            for record in records
            if isinstance(record, Mapping)
            and isinstance((labels := record.get("labels")), Mapping)
            and isinstance(labels.get("app"), str)
        }
    )
    return {
        "root_filter_dnf": branches,
        "allowed_apps": allowed_apps,
        "returned_apps": returned_apps,
        "scopes_without_hits": sorted(set(allowed_apps) - set(returned_apps)),
        "truncated": bool(result.get("truncated")),
        "record_count": result.get("record_count"),
    }


def _trace_correlation_claim(
    *,
    connector_snapshot_id: int,
    candidate_id: int,
    access_decision_id: int,
    full_scope_discovery: bool,
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "claim_type": "sealed_trace_correlation",
        "server_generated": True,
        "sealed_value_ref": "incident.trace_id",
        "match_mode": "exact_line_filter",
        "provider_filter_semantics": "returned_records_satisfy_exact_filter",
        "connector_snapshot_id": connector_snapshot_id,
        "candidate_id": candidate_id,
        "access_decision_id": access_decision_id,
        "query_scope": (
            "connector_root" if full_scope_discovery else "evidence_identified_component"
        ),
        "scope_coverage": dict(coverage),
    }


def _event_attributes(value: str) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, Mapping):
        attributes["event"] = dict(parsed)
    for key, pattern in _KEY_VALUE.items():
        match = pattern.search(value)
        if match is not None:
            attributes[key] = match.group(1)[:2_000]
    return attributes


def _identity_matches_repository(identity: str, repository: GitRepository) -> bool:
    normalized = identity.casefold()
    names = {
        repository.name.casefold(),
        repository.full_name.rsplit("/", 1)[-1].casefold(),
    }
    names.update(
        "-".join(value.split("-")[1:]) for value in tuple(names) if len(value.split("-")) > 2
    )
    return any(normalized == name or normalized in name or name in normalized for name in names)


def _revision_hints(labels: Mapping[str, Any]) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    for key in ("revision", "source_revision", "git_sha", "commit_sha"):
        value = labels.get(key)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value):
            hints.append({"origin": "runtime_observed", "sha": value})
    return hints


def _loki_timestamp(value: Any) -> datetime:
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return datetime.now(UTC)
    if numeric > 10**14:
        numeric /= 1_000_000_000
    elif numeric > 10**11:
        numeric /= 1_000
    try:
        return datetime.fromtimestamp(numeric, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return datetime.now(UTC)


def _artifact_kind(language: str) -> str:
    return {
        "logql": "normalized_log_result",
        "elasticsearch_query_dsl": "normalized_search_result",
        "opensearch_query_dsl": "normalized_search_result",
        "sql": "normalized_sql_result",
        "https": "normalized_https_result",
        "command": "normalized_command_result",
    }[language]
