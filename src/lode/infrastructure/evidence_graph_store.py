"""Ownership-safe persistence for investigation-local evidence graph projections."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.evidence_graph import GraphProjection
from lode.db.models import (
    EvidenceArtifact,
    EvidenceLink,
    Investigation,
    InvestigationConnectorSnapshot,
    ObservedEntity,
    ObservedEvent,
    ObservedRelation,
    ResourceObservation,
)


class EvidenceGraphStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def persist(
        self,
        *,
        workspace_id: int,
        investigation_id: int,
        projection: GraphProjection,
    ) -> None:
        investigation = await self.session.get(Investigation, investigation_id)
        if investigation is None or investigation.workspace_id != workspace_id:
            raise ValueError("evidence graph investigation ownership failed")
        artifact_ids = {event.evidence_artifact_id for event in projection.timeline} | {
            ref for relation in projection.relations for ref in relation.evidence_refs
        }
        owned_artifacts = set(
            (
                await self.session.execute(
                    select(EvidenceArtifact.id).where(
                        EvidenceArtifact.investigation_id == investigation_id,
                        EvidenceArtifact.id.in_(artifact_ids or {-1}),
                    )
                )
            ).scalars()
        )
        if owned_artifacts != artifact_ids:
            raise ValueError("evidence graph artifact ownership failed")
        snapshot_ids = {event.connector_snapshot_id for event in projection.timeline}
        snapshot_rows = tuple(
            (
                await self.session.execute(
                    select(InvestigationConnectorSnapshot).where(
                        InvestigationConnectorSnapshot.investigation_id == investigation_id,
                        InvestigationConnectorSnapshot.id.in_(snapshot_ids or {-1}),
                    )
                )
            )
            .scalars()
            .all()
        )
        snapshot_by_id = {row.id: row for row in snapshot_rows}
        if set(snapshot_by_id) != snapshot_ids:
            raise ValueError("evidence graph connector ownership failed")

        event_rows: dict[tuple[int, str], ObservedEvent] = {}
        for event in projection.timeline:
            key = (event.connector_snapshot_id, event.provider_position)
            row = (
                await self.session.execute(
                    select(ObservedEvent).where(
                        ObservedEvent.investigation_id == investigation_id,
                        ObservedEvent.connector_snapshot_id == event.connector_snapshot_id,
                        ObservedEvent.provider_position == event.provider_position,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = ObservedEvent(
                    investigation_id=investigation_id,
                    occurred_at=event.occurred_at,
                    connector_snapshot_id=event.connector_snapshot_id,
                    provider_position=event.provider_position,
                    raw_excerpt_masked=event.raw_excerpt_masked,
                    attributes_masked=dict(event.attributes_masked),
                    resource_attributes_masked=dict(event.resource_attributes_masked),
                    trace_match=dict(event.trace_match),
                    component_candidates=[dict(value) for value in event.component_candidates],
                    relation_hints=[dict(value) for value in event.relation_hints],
                    revision_hints=[dict(value) for value in event.revision_hints],
                    provider_metadata=dict(event.provider_metadata),
                    evidence_artifact_id=event.evidence_artifact_id,
                )
                self.session.add(row)
                await self.session.flush()
            event_rows[key] = row

        entity_rows: dict[str, ObservedEntity] = {}
        for value in projection.entities:
            row = (
                await self.session.execute(
                    select(ObservedEntity).where(
                        ObservedEntity.investigation_id == investigation_id,
                        ObservedEntity.stable_key == value.stable_key,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = ObservedEntity(
                    investigation_id=investigation_id,
                    entity_kind=value.entity_kind,
                    stable_key=value.stable_key,
                    display_name=value.display_name,
                    component_snapshot_id=value.component_snapshot_id,
                    identity_status=value.identity_status,
                    provider_identity_masked=dict(value.provider_identity_masked),
                    attributes_masked=dict(value.attributes_masked),
                    evidence_refs=list(value.evidence_refs),
                )
                self.session.add(row)
                await self.session.flush()
            entity_rows[value.stable_key] = row

        relation_rows: list[ObservedRelation] = []
        for value in projection.relations:
            source = entity_rows.get(value.source_stable_key)
            target = entity_rows.get(value.target_stable_key)
            if source is None or target is None:
                raise ValueError("evidence graph relation endpoint is missing")
            row = (
                await self.session.execute(
                    select(ObservedRelation).where(
                        ObservedRelation.investigation_id == investigation_id,
                        ObservedRelation.source_entity_id == source.id,
                        ObservedRelation.target_entity_id == target.id,
                        ObservedRelation.relation_kind == value.kind.value,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = ObservedRelation(
                    investigation_id=investigation_id,
                    source_entity_id=source.id,
                    target_entity_id=target.id,
                    relation_kind=value.kind.value,
                    status="observed",
                    evidence_refs=list(value.evidence_refs),
                    relation_basis=dict(value.basis),
                )
                self.session.add(row)
                await self.session.flush()
            relation_rows.append(row)

        for value in projection.resource_observations:
            snapshot = snapshot_by_id[value.connector_snapshot_id]
            source_revision = f"investigation:{investigation_id}"
            existing = (
                await self.session.execute(
                    select(ResourceObservation.id).where(
                        ResourceObservation.source_ref == value.source_ref,
                        ResourceObservation.source_revision == source_revision,
                        ResourceObservation.content_hash == value.content_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self.session.add(
                    ResourceObservation(
                        workspace_id=workspace_id,
                        source_kind="connector_event",
                        source_ref=value.source_ref,
                        observation_kind="log_identity",
                        structured_payload=dict(value.structured_payload),
                        content_hash=value.content_hash,
                        repository_id=None,
                        source_revision=source_revision,
                        path=None,
                        connector_id=snapshot.connector_id,
                        artifact_id=value.artifact_id,
                        root_provenance_id=value.root_provenance_id,
                        source_family="runtime_connector",
                        trust_class="observed",
                        valid_from=None,
                        valid_until=None,
                        observed_at=datetime.now(UTC),
                        parser_name="evidence-graph-projector",
                        parser_version="1",
                    )
                )

        await self._links(
            investigation_id,
            "event",
            [(row.id, row.evidence_artifact_id) for row in event_rows.values()],
        )
        await self._links(
            investigation_id,
            "entity",
            [
                (row.id, artifact_id)
                for row in entity_rows.values()
                for artifact_id in row.evidence_refs
            ],
        )
        await self._links(
            investigation_id,
            "relation",
            [(row.id, artifact_id) for row in relation_rows for artifact_id in row.evidence_refs],
        )
        await self.session.commit()

    async def _links(
        self,
        investigation_id: int,
        source_type: str,
        values: Sequence[tuple[int, int]],
    ) -> None:
        for source_id, artifact_id in values:
            existing = (
                await self.session.execute(
                    select(EvidenceLink.id).where(
                        EvidenceLink.source_type == source_type,
                        EvidenceLink.source_id == source_id,
                        EvidenceLink.artifact_id == artifact_id,
                        EvidenceLink.relation == "observed_in",
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self.session.add(
                    EvidenceLink(
                        investigation_id=investigation_id,
                        source_type=source_type,
                        source_id=source_id,
                        artifact_id=artifact_id,
                        relation="observed_in",
                    )
                )
