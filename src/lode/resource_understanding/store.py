"""Transactional persistence and automatic ResourceGraph publication."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models import (
    BuildUnit,
    Component,
    ComponentSourceBinding,
    IdentityResolution,
    ResourceGraphRevision,
    ResourceGraphRevisionMember,
    ResourceObservation,
    SemanticAnnotation,
    Workspace,
    WorkspaceRepositoryBinding,
)
from lode.metrics import (
    IDENTITY_RESOLUTIONS,
    RESOURCE_EVENTS,
    RESOURCE_INVALIDATION_LATENCY,
)
from lode.resource_understanding.types import (
    IdentityResolutionDraft,
    ScanResult,
    SemanticAnnotationDraft,
    content_hash,
    repository_candidate_namespace,
)
from lode.resource_understanding.validator import ResourceIdentityValidator


@dataclass(frozen=True, slots=True)
class BoundRepositoryScan:
    repository_binding_id: int
    repository_id: int
    scan: ScanResult


@dataclass(frozen=True, slots=True)
class PublishedResourceGraph:
    revision_id: int
    revision: int
    reused: bool
    observation_count: int
    resolution_count: int
    issue_count: int


class ResourceGraphStore:
    """Publish validated knowledge without mutating prior graph revisions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.validator = ResourceIdentityValidator()

    async def publish(
        self,
        *,
        workspace_id: int,
        scans: Sequence[BoundRepositoryScan],
        annotations: Iterable[SemanticAnnotationDraft] = (),
        code_binding_ids: set[int] | None = None,
        allow_inactive_binding_ids: set[int] | None = None,
        prompt_revision: str = "resource-understanding.1",
    ) -> PublishedResourceGraph:
        started = monotonic()
        if not scans:
            raise ValueError("at least one repository scan is required")
        scans = tuple(sorted(scans, key=lambda item: item.repository_binding_id))
        workspace = (
            await self.session.execute(
                select(Workspace).where(Workspace.id == workspace_id).with_for_update()
            )
        ).scalar_one_or_none()
        if workspace is None:
            raise ValueError("workspace does not exist")

        bindings = await self._validate_bindings(
            workspace_id,
            scans,
            allow_inactive_binding_ids=allow_inactive_binding_ids,
        )
        if code_binding_ids is None:
            code_binding_ids = {
                binding_id
                for binding_id, binding in bindings.items()
                if binding.analysis_mode == "code"
            }
        if not code_binding_ids.issubset({item.repository_binding_id for item in scans}):
            raise ValueError("code scan bindings are not part of the publication")
        code_scans = [item.scan for item in scans if item.repository_binding_id in code_binding_ids]
        annotation_list = tuple(annotations)
        drafts = list(self.validator.validate_many(code_scans, annotation_list))
        candidate_bindings = {
            unit.candidate_key: item.repository_binding_id
            for item in scans
            for unit in item.scan.build_units
        }
        drafts = [self._enrich_resolution(item, candidate_bindings) for item in drafts]
        drafts = await self._apply_current_alias_conflicts(workspace_id, drafts)

        observation_ids = await self._persist_observations(workspace_id, scans)
        annotation_ids = await self._persist_annotations(
            workspace_id, annotation_list, observation_ids, prompt_revision
        )
        resolution_rows = await self._persist_resolutions(
            workspace_id, drafts, observation_ids, annotation_ids
        )
        await self._materialize(workspace_id, drafts, resolution_rows, candidate_bindings)

        scanned_binding_ids = {item.repository_binding_id for item in scans}
        current = await self._current_resolution_rows(workspace_id)
        new_keys = {(row.stable_key, row.resolution_kind) for row in resolution_rows}
        members = [
            row
            for row in current
            if (row.stable_key, row.resolution_kind) not in new_keys
            and not self._belongs_to_bindings(row.resolved_payload, scanned_binding_ids)
        ]
        members.extend(resolution_rows)
        members = list({row.id: row for row in members}.values())
        members.sort(key=lambda row: (row.resolution_kind, row.stable_key, row.id))

        input_hash = content_hash(
            {
                "scans": [
                    {
                        "repository_binding_id": item.repository_binding_id,
                        "input_hash": item.scan.input_hash,
                    }
                    for item in scans
                ],
                "members": [row.resolution_hash for row in members],
                "validator_version": self.validator.version,
            }
        )
        latest = (
            await self.session.execute(
                select(ResourceGraphRevision)
                .where(ResourceGraphRevision.workspace_id == workspace_id)
                .order_by(ResourceGraphRevision.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None and latest.input_hash == input_hash:
            await self.session.commit()
            RESOURCE_EVENTS.labels(kind="graph_revision", outcome="reused").inc()
            return PublishedResourceGraph(
                latest.id,
                latest.revision,
                True,
                len(observation_ids),
                len(members),
                sum(len(item.scan.issues) for item in scans),
            )

        previous_ids: set[int] = set()
        if latest is not None:
            previous_ids = set(
                (
                    await self.session.execute(
                        select(ResourceGraphRevisionMember.identity_resolution_id).where(
                            ResourceGraphRevisionMember.resource_graph_revision_id == latest.id
                        )
                    )
                ).scalars()
            )
        member_ids = {row.id for row in members}
        removed_ids = previous_ids - member_ids
        if removed_ids:
            await self.session.execute(
                update(IdentityResolution)
                .where(
                    IdentityResolution.id.in_(removed_ids),
                    IdentityResolution.valid_until.is_(None),
                )
                .values(
                    valid_until=datetime.now(UTC),
                    invalidation_reason="superseded_by_resource_graph_revision",
                )
            )
        graph = ResourceGraphRevision(
            workspace_id=workspace_id,
            revision=1 if latest is None else latest.revision + 1,
            parent_revision_id=None if latest is None else latest.id,
            input_hash=input_hash,
            validator_version=self.validator.version,
            diff={
                "added_resolution_ids": sorted(member_ids - previous_ids),
                "removed_resolution_ids": sorted(removed_ids),
                "issues": [asdict(issue) for item in scans for issue in item.scan.issues],
            },
            published_at=datetime.now(UTC),
        )
        self.session.add(graph)
        await self.session.flush()
        self.session.add_all(
            [
                ResourceGraphRevisionMember(
                    resource_graph_revision_id=graph.id,
                    identity_resolution_id=row.id,
                    member_kind=row.resolution_kind,
                )
                for row in members
            ]
        )
        await self._reconcile_materialized_state(workspace_id, members)
        await self.session.commit()
        RESOURCE_EVENTS.labels(kind="observation", outcome="persisted").inc(len(observation_ids))
        RESOURCE_EVENTS.labels(kind="graph_revision", outcome="published").inc()
        for row in resolution_rows:
            IDENTITY_RESOLUTIONS.labels(status=row.status).inc()
        if removed_ids:
            RESOURCE_INVALIDATION_LATENCY.observe(monotonic() - started)
        return PublishedResourceGraph(
            graph.id,
            graph.revision,
            False,
            len(observation_ids),
            len(members),
            sum(len(item.scan.issues) for item in scans),
        )

    async def _validate_bindings(
        self,
        workspace_id: int,
        scans: Sequence[BoundRepositoryScan],
        *,
        allow_inactive_binding_ids: set[int] | None = None,
    ) -> dict[int, WorkspaceRepositoryBinding]:
        ids = [item.repository_binding_id for item in scans]
        if len(ids) != len(set(ids)):
            raise ValueError("repository bindings must be unique per publication")
        allowed = allow_inactive_binding_ids or set()
        rows = (
            (
                await self.session.execute(
                    select(WorkspaceRepositoryBinding).where(
                        WorkspaceRepositoryBinding.id.in_(ids),
                        WorkspaceRepositoryBinding.workspace_id == workspace_id,
                        (WorkspaceRepositoryBinding.state == "active")
                        | WorkspaceRepositoryBinding.id.in_(allowed),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        if set(ids) != set(by_id):
            raise ValueError("scan references an inactive or foreign repository binding")
        for item in scans:
            if by_id[item.repository_binding_id].repository_id != item.repository_id:
                raise ValueError("scan repository does not match its binding")
            namespace = f"{repository_candidate_namespace(item.repository_binding_id)}/"
            if any(not unit.candidate_key.startswith(namespace) for unit in item.scan.build_units):
                raise ValueError("scan build units are not binding-namespaced")
            if any(not obs.source_ref.startswith(namespace) for obs in item.scan.observations):
                raise ValueError("scan observations are not binding-namespaced")
        return by_id

    async def _persist_observations(
        self,
        workspace_id: int,
        scans: Sequence[BoundRepositoryScan],
    ) -> dict[str, int]:
        now = datetime.now(UTC)
        result: dict[str, int] = {}
        for bound in scans:
            for item in bound.scan.observations:
                values = {
                    "workspace_id": workspace_id,
                    "source_kind": item.source_kind,
                    "source_ref": item.source_ref,
                    "observation_kind": item.observation_kind,
                    "structured_payload": dict(item.structured_payload),
                    "content_hash": item.content_hash,
                    "repository_id": bound.repository_id,
                    "source_revision": bound.scan.source_revision,
                    "path": item.path,
                    "root_provenance_id": item.root_provenance_id,
                    "source_family": item.source_family,
                    "trust_class": item.trust_class,
                    "valid_from": now,
                    "observed_at": now,
                    "parser_name": item.parser_name,
                    "parser_version": item.parser_version,
                }
                row_id = (
                    await self.session.execute(
                        pg_insert(ResourceObservation)
                        .values(**values)
                        .on_conflict_do_nothing(constraint="uq_resource_observation_source")
                        .returning(ResourceObservation.id)
                    )
                ).scalar_one_or_none()
                if row_id is None:
                    row_id = (
                        await self.session.execute(
                            select(ResourceObservation.id).where(
                                ResourceObservation.source_ref == item.source_ref,
                                ResourceObservation.source_revision == bound.scan.source_revision,
                                ResourceObservation.content_hash == item.content_hash,
                            )
                        )
                    ).scalar_one()
                result[item.source_ref] = row_id
        return result

    async def _persist_annotations(
        self,
        workspace_id: int,
        annotations: tuple[SemanticAnnotationDraft, ...],
        observation_ids: dict[str, int],
        prompt_revision: str,
    ) -> list[int]:
        result: list[int] = []
        for item in annotations:
            refs = [observation_ids[ref] for ref in item.observation_refs]
            payload = asdict(item)
            existing = (
                await self.session.execute(
                    select(SemanticAnnotation.id).where(
                        SemanticAnnotation.workspace_id == workspace_id,
                        SemanticAnnotation.annotation_kind == item.annotation_kind,
                        SemanticAnnotation.structured_payload == payload,
                        SemanticAnnotation.observation_refs == refs,
                        SemanticAnnotation.prompt_revision == prompt_revision,
                        SemanticAnnotation.superseded_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                row = SemanticAnnotation(
                    workspace_id=workspace_id,
                    annotation_kind=item.annotation_kind,
                    structured_payload=payload,
                    observation_refs=refs,
                    prompt_revision=prompt_revision,
                )
                self.session.add(row)
                await self.session.flush()
                existing = row.id
            result.append(existing)
        return result

    async def _persist_resolutions(
        self,
        workspace_id: int,
        drafts: list[IdentityResolutionDraft],
        observation_ids: dict[str, int],
        annotation_ids: list[int],
    ) -> list[IdentityResolution]:
        result: list[IdentityResolution] = []
        now = datetime.now(UTC)
        for item in drafts:
            obs_refs = [observation_ids[ref] for ref in item.observation_refs]
            ann_refs = [annotation_ids[index] for index in item.annotation_indexes]
            resolution_hash = content_hash(
                {
                    "stable_key": item.stable_key,
                    "kind": item.resolution_kind,
                    "status": item.status,
                    "payload": item.resolved_payload,
                    "basis": item.evidence_basis,
                    "observations": obs_refs,
                    "annotations": ann_refs,
                    "provenance": item.root_provenance_refs,
                    "validator": self.validator.version,
                }
            )
            row = (
                await self.session.execute(
                    select(IdentityResolution).where(
                        IdentityResolution.workspace_id == workspace_id,
                        IdentityResolution.resolution_hash == resolution_hash,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = IdentityResolution(
                    workspace_id=workspace_id,
                    stable_key=item.stable_key,
                    resolution_kind=item.resolution_kind,
                    resolved_payload=dict(item.resolved_payload),
                    status=item.status,
                    evidence_basis=dict(item.evidence_basis),
                    observation_refs=obs_refs,
                    annotation_refs=ann_refs,
                    root_provenance_refs=list(item.root_provenance_refs),
                    validator_version=self.validator.version,
                    valid_from=now,
                    resolution_hash=resolution_hash,
                )
                self.session.add(row)
                await self.session.flush()
            result.append(row)
        return result

    async def _materialize(
        self,
        workspace_id: int,
        drafts: list[IdentityResolutionDraft],
        rows: list[IdentityResolution],
        candidate_bindings: dict[str, int],
    ) -> None:
        build_rows: dict[str, BuildUnit] = {}
        for item in drafts:
            if item.resolution_kind != "build_unit":
                continue
            payload = item.resolved_payload
            stable_key = item.stable_key
            row = (
                await self.session.execute(
                    select(BuildUnit).where(
                        BuildUnit.workspace_id == workspace_id,
                        BuildUnit.stable_key == stable_key,
                    )
                )
            ).scalar_one_or_none()
            values = {
                "repository_binding_id": payload["repository_binding_id"],
                "source_root": payload["source_root"],
                "build_system": payload["build_system"],
                "manifest_paths": list(payload["manifest_paths"]),
                "entrypoints": list(payload["entrypoints"]),
                "artifact_hints": dict(payload["artifact_hints"]),
                "discovery_basis": dict(item.evidence_basis),
                "identity_status": item.status,
                "state": "active",
                "ownership_priority": payload["ownership_priority"],
            }
            if row is None:
                row = BuildUnit(workspace_id=workspace_id, stable_key=stable_key, **values)
                self.session.add(row)
                await self.session.flush()
            elif any(getattr(row, key) != value for key, value in values.items()):
                for key, value in values.items():
                    setattr(row, key, value)
                row.revision += 1
            build_rows[stable_key] = row

        component_rows: dict[str, Component] = {}
        for item in drafts:
            if item.resolution_kind != "component":
                continue
            payload = item.resolved_payload
            families = list(item.evidence_basis.get("source_families", []))
            row = (
                await self.session.execute(
                    select(Component).where(
                        Component.workspace_id == workspace_id,
                        Component.stable_key == item.stable_key,
                    )
                )
            ).scalar_one_or_none()
            values = {
                "display_name": payload["display_name"],
                "kind": payload["kind"],
                "description": payload["description"],
                "identity_status": item.status,
                "state": "active",
                "discovery_basis": dict(item.evidence_basis),
                "root_provenance_families": families,
            }
            if row is None:
                row = Component(workspace_id=workspace_id, stable_key=item.stable_key, **values)
                self.session.add(row)
                await self.session.flush()
            elif any(getattr(row, key) != value for key, value in values.items()):
                for key, value in values.items():
                    setattr(row, key, value)
                row.revision += 1
            component_rows[item.stable_key] = row

        if component_rows:
            await self.session.execute(
                delete(ComponentSourceBinding).where(
                    ComponentSourceBinding.component_id.in_(
                        [row.id for row in component_rows.values()]
                    )
                )
            )

        for item in drafts:
            if item.resolution_kind != "component_source_binding":
                continue
            payload = item.resolved_payload
            component = component_rows[payload["component_key"]]
            build = build_rows.get(payload["build_unit_key"])
            if build is None:
                build = (
                    await self.session.execute(
                        select(BuildUnit).where(
                            BuildUnit.workspace_id == workspace_id,
                            BuildUnit.stable_key == payload["build_unit_key"],
                        )
                    )
                ).scalar_one()
            existing = await self.session.get(
                ComponentSourceBinding,
                (component.id, build.id, payload["role"]),
            )
            if existing is None:
                self.session.add(
                    ComponentSourceBinding(
                        component_id=component.id,
                        build_unit_id=build.id,
                        role=payload["role"],
                        path_prefix=payload["path_prefix"],
                    )
                )
            else:
                existing.path_prefix = payload["path_prefix"]

    async def _apply_current_alias_conflicts(
        self,
        workspace_id: int,
        drafts: list[IdentityResolutionDraft],
    ) -> list[IdentityResolutionDraft]:
        current = await self._current_resolution_rows(workspace_id)
        owners: dict[str, set[str]] = {}
        replacing = {item.stable_key for item in drafts if item.resolution_kind == "component"}
        for row in current:
            if row.resolution_kind != "component" or row.stable_key in replacing:
                continue
            for alias in row.resolved_payload.get("aliases", []):
                owners.setdefault(alias, set()).add(row.stable_key)
        result: list[IdentityResolutionDraft] = []
        for item in drafts:
            if item.resolution_kind != "component":
                result.append(item)
                continue
            conflicts = sorted(
                alias for alias in item.resolved_payload.get("aliases", []) if owners.get(alias)
            )
            if not conflicts:
                result.append(item)
                continue
            basis = dict(item.evidence_basis)
            basis["alias_conflicts"] = sorted(
                set(basis.get("alias_conflicts", [])) | set(conflicts)
            )
            basis["conflicting_component_keys"] = sorted(
                {owner for alias in conflicts for owner in owners[alias]}
            )
            result.append(replace(item, status="ambiguous", evidence_basis=basis))
        ambiguous_components = {
            item.stable_key
            for item in result
            if item.resolution_kind == "component" and item.status == "ambiguous"
        }
        return [
            replace(item, status="ambiguous")
            if item.resolution_kind == "component_source_binding"
            and item.resolved_payload.get("component_key") in ambiguous_components
            else item
            for item in result
        ]

    async def _reconcile_materialized_state(
        self,
        workspace_id: int,
        members: list[IdentityResolution],
    ) -> None:
        build_keys = {row.stable_key for row in members if row.resolution_kind == "build_unit"}
        component_keys = {row.stable_key for row in members if row.resolution_kind == "component"}
        await self.session.execute(
            update(BuildUnit)
            .where(
                BuildUnit.workspace_id == workspace_id,
                BuildUnit.state == "active",
                BuildUnit.stable_key.not_in(build_keys) if build_keys else True,
            )
            .values(state="disabled", revision=BuildUnit.revision + 1)
        )
        await self.session.execute(
            update(Component)
            .where(
                Component.workspace_id == workspace_id,
                Component.state == "active",
                Component.stable_key.not_in(component_keys) if component_keys else True,
            )
            .values(state="disabled", revision=Component.revision + 1)
        )

    @staticmethod
    def _enrich_resolution(
        item: IdentityResolutionDraft,
        candidate_bindings: dict[str, int],
    ) -> IdentityResolutionDraft:
        payload = dict(item.resolved_payload)
        binding_ids: set[int] = set()
        if item.resolution_kind == "build_unit":
            binding_ids.add(candidate_bindings[payload["candidate_key"]])
            payload["repository_binding_id"] = next(iter(binding_ids))
        elif item.resolution_kind == "component":
            binding_ids.update(candidate_bindings[key] for key in payload["build_unit_keys"])
            payload["repository_binding_ids"] = sorted(binding_ids)
        elif item.resolution_kind == "component_source_binding":
            binding_ids.add(candidate_bindings[payload["build_unit_key"]])
            payload["repository_binding_id"] = next(iter(binding_ids))
        return replace(item, resolved_payload=payload)

    async def _current_resolution_rows(self, workspace_id: int) -> list[IdentityResolution]:
        latest = (
            await self.session.execute(
                select(ResourceGraphRevision)
                .where(ResourceGraphRevision.workspace_id == workspace_id)
                .order_by(ResourceGraphRevision.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is None:
            return []
        return list(
            (
                await self.session.execute(
                    select(IdentityResolution)
                    .join(
                        ResourceGraphRevisionMember,
                        ResourceGraphRevisionMember.identity_resolution_id == IdentityResolution.id,
                    )
                    .where(ResourceGraphRevisionMember.resource_graph_revision_id == latest.id)
                )
            ).scalars()
        )

    @staticmethod
    def _belongs_to_bindings(payload: dict[str, Any], binding_ids: set[int]) -> bool:
        direct = payload.get("repository_binding_id")
        many = payload.get("repository_binding_ids", [])
        return direct in binding_ids or bool(set(many) & binding_ids)
