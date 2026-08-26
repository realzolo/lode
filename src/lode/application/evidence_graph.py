"""Deterministic projection from normalized events into investigation evidence graphs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from lode.domain.investigation import (
    NormalizedLogEvent,
    RelationEvidence,
    canonical_hash,
)
from lode.domain.types import RelationKind


@dataclass(frozen=True, slots=True)
class FrozenIdentityAlias:
    alias: str
    component_snapshot_id: int
    component_stable_key: str
    display_name: str
    status: Literal["verified", "provisional", "ambiguous"]


@dataclass(frozen=True, slots=True)
class ObservedEntityDraft:
    entity_kind: str
    stable_key: str
    display_name: str
    component_snapshot_id: int | None
    identity_status: str
    provider_identity_masked: Mapping[str, Any]
    attributes_masked: Mapping[str, Any]
    evidence_refs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ResourceObservationDraft:
    source_ref: str
    structured_payload: Mapping[str, Any]
    content_hash: str
    connector_snapshot_id: int
    artifact_id: int
    root_provenance_id: str


@dataclass(frozen=True, slots=True)
class ProjectedEvent:
    event: NormalizedLogEvent
    entities: tuple[ObservedEntityDraft, ...]
    resource_observations: tuple[ResourceObservationDraft, ...]


@dataclass(frozen=True, slots=True)
class GraphProjection:
    timeline: tuple[NormalizedLogEvent, ...]
    entities: tuple[ObservedEntityDraft, ...]
    relations: tuple[RelationEvidence, ...]
    resource_observations: tuple[ResourceObservationDraft, ...]


class EvidenceGraphProjector:
    def project(
        self,
        events: Sequence[NormalizedLogEvent],
        *,
        aliases: Sequence[FrozenIdentityAlias],
    ) -> GraphProjection:
        timeline = tuple(sorted(events, key=lambda value: value.timeline_key))
        alias_index: dict[str, list[FrozenIdentityAlias]] = defaultdict(list)
        for alias in aliases:
            alias_index[alias.alias].append(alias)

        projected = [self._project_event(event, alias_index) for event in timeline]
        entities = self._dedupe_entities(entity for item in projected for entity in item.entities)
        entity_keys = {entity.stable_key for entity in entities}
        relations = self._derive_relations(timeline, entity_keys)
        topic_entities = tuple(
            ObservedEntityDraft(
                entity_kind="topic",
                stable_key=relation.target_stable_key,
                display_name=str(relation.basis.get("topic", "topic")),
                component_snapshot_id=None,
                identity_status="verified",
                provider_identity_masked={"topic": relation.basis.get("topic")},
                attributes_masked={},
                evidence_refs=relation.evidence_refs,
            )
            for relation in relations
            if relation.kind in {RelationKind.PUBLISHED_TO, RelationKind.CONSUMED_FROM}
            and relation.target_stable_key.startswith("topic:")
        )
        entities = self._dedupe_entities((*entities, *topic_entities))
        observations = tuple(
            observation for item in projected for observation in item.resource_observations
        )
        return GraphProjection(timeline, entities, relations, observations)

    def _project_event(
        self,
        event: NormalizedLogEvent,
        aliases: Mapping[str, Sequence[FrozenIdentityAlias]],
    ) -> ProjectedEvent:
        entities: list[ObservedEntityDraft] = []
        observations: list[ResourceObservationDraft] = []
        for candidate in event.component_candidates:
            identity = candidate.get("identity")
            location = candidate.get("location")
            if not isinstance(identity, str) or not identity or not isinstance(location, str):
                continue
            matches = tuple(aliases.get(identity, ()))
            provider_identity = {"identity": identity, "location": location}
            if len(matches) == 1 and matches[0].status != "ambiguous":
                match = matches[0]
                entities.append(
                    ObservedEntityDraft(
                        entity_kind="component",
                        stable_key=match.component_stable_key,
                        display_name=match.display_name,
                        component_snapshot_id=match.component_snapshot_id,
                        identity_status=match.status,
                        provider_identity_masked=provider_identity,
                        attributes_masked={},
                        evidence_refs=(event.evidence_artifact_id,),
                    )
                )
                continue
            stable_key = f"unknown:{canonical_hash({'identity': identity})[:24]}"
            status = "ambiguous" if matches else "unknown"
            entities.append(
                ObservedEntityDraft(
                    entity_kind="unknown_component",
                    stable_key=stable_key,
                    display_name=identity,
                    component_snapshot_id=None,
                    identity_status=status,
                    provider_identity_masked=provider_identity,
                    attributes_masked={
                        "candidate_component_snapshot_ids": [
                            match.component_snapshot_id for match in matches
                        ]
                    },
                    evidence_refs=(event.evidence_artifact_id,),
                )
            )
            payload = {
                "identity": identity,
                "location": location,
                "candidate_component_snapshot_ids": [
                    match.component_snapshot_id for match in matches
                ],
                "identity_status": status,
            }
            observations.append(
                ResourceObservationDraft(
                    source_ref=(
                        f"investigation-event:{event.connector_snapshot_id}:"
                        f"{event.provider_position}:{canonical_hash(payload)[:16]}"
                    ),
                    structured_payload=payload,
                    content_hash=canonical_hash(payload),
                    connector_snapshot_id=event.connector_snapshot_id,
                    artifact_id=event.evidence_artifact_id,
                    root_provenance_id=(
                        f"connector:{event.connector_snapshot_id}:"
                        f"artifact:{event.evidence_artifact_id}"
                    ),
                )
            )
        return ProjectedEvent(event, tuple(entities), tuple(observations))

    def _derive_relations(
        self,
        events: Sequence[NormalizedLogEvent],
        entity_keys: set[str],
    ) -> tuple[RelationEvidence, ...]:
        relations: list[RelationEvidence] = []
        http_clients: list[tuple[NormalizedLogEvent, Mapping[str, Any]]] = []
        http_servers: list[tuple[NormalizedLogEvent, Mapping[str, Any]]] = []
        kafka_producers: list[tuple[NormalizedLogEvent, Mapping[str, Any]]] = []
        kafka_consumers: list[tuple[NormalizedLogEvent, Mapping[str, Any]]] = []
        for event in events:
            for hint in event.relation_hints:
                hint_type = hint.get("type")
                if hint_type in {"span_parent", "caller_callee", "descriptor_rule"}:
                    relation = self._direct_relation(event, hint, entity_keys)
                    if relation is not None:
                        relations.append(relation)
                elif hint_type == "http_client":
                    http_clients.append((event, hint))
                elif hint_type == "http_server":
                    http_servers.append((event, hint))
                elif hint_type == "kafka_producer":
                    kafka_producers.append((event, hint))
                elif hint_type == "kafka_consumer":
                    kafka_consumers.append((event, hint))
        relations.extend(self._http_relations(http_clients, http_servers, entity_keys))
        relations.extend(self._kafka_relations(kafka_producers, kafka_consumers, entity_keys))
        unique: dict[tuple[str, str, str], RelationEvidence] = {}
        for relation in relations:
            key = (
                relation.kind.value,
                relation.source_stable_key,
                relation.target_stable_key,
            )
            existing = unique.get(key)
            if existing is None:
                unique[key] = relation
                continue
            refs = tuple(sorted(set(existing.evidence_refs) | set(relation.evidence_refs)))
            unique[key] = RelationEvidence(
                relation.kind,
                relation.source_stable_key,
                relation.target_stable_key,
                refs,
                {"matches": [dict(existing.basis), dict(relation.basis)]},
            )
        return tuple(unique[key] for key in sorted(unique))

    def _direct_relation(
        self,
        event: NormalizedLogEvent,
        hint: Mapping[str, Any],
        entity_keys: set[str],
    ) -> RelationEvidence | None:
        hint_type = hint.get("type")
        if hint_type == "span_parent":
            source = hint.get("parent_entity")
            target = hint.get("child_entity")
            kind = RelationKind.CALLED
            proof = {"type": hint_type, "parent_span_id": hint.get("parent_span_id")}
        elif hint_type == "caller_callee":
            source = hint.get("caller_entity")
            target = hint.get("callee_entity")
            kind = RelationKind.CALLED
            proof = {"type": hint_type, "attribute": hint.get("attribute")}
        else:
            source = hint.get("source_entity")
            target = hint.get("target_entity")
            try:
                kind = RelationKind(str(hint.get("relation_kind")))
            except ValueError:
                return None
            refs = hint.get("evidence_refs")
            if not isinstance(refs, tuple | list) or not refs:
                return None
            proof = {
                "type": hint_type,
                "descriptor_id": hint.get("descriptor_id"),
                "evidence_refs": list(refs),
            }
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or source not in entity_keys
            or target not in entity_keys
            or source == target
        ):
            return None
        evidence_refs = tuple(
            sorted(
                {
                    event.evidence_artifact_id,
                    *(
                        value
                        for value in hint.get("evidence_refs", ())
                        if isinstance(value, int) and value > 0
                    ),
                }
            )
        )
        return RelationEvidence(kind, source, target, evidence_refs, proof)

    def _http_relations(
        self,
        clients: Sequence[tuple[NormalizedLogEvent, Mapping[str, Any]]],
        servers: Sequence[tuple[NormalizedLogEvent, Mapping[str, Any]]],
        entity_keys: set[str],
    ) -> list[RelationEvidence]:
        output: list[RelationEvidence] = []
        for client_event, client in clients:
            for server_event, server in servers:
                source = client.get("source_entity")
                target = server.get("target_entity")
                if not self._valid_endpoints(source, target, entity_keys):
                    continue
                client_key = (
                    client.get("method"),
                    client.get("route"),
                    client_event.trace_match.get("value_hash"),
                )
                server_key = (
                    server.get("method"),
                    server.get("route"),
                    server_event.trace_match.get("value_hash"),
                )
                if client_key != server_key or None in client_key:
                    continue
                if abs(client_event.occurred_at - server_event.occurred_at) > timedelta(seconds=30):
                    continue
                output.append(
                    RelationEvidence(
                        RelationKind.CALLED,
                        str(source),
                        str(target),
                        tuple(
                            sorted(
                                {
                                    client_event.evidence_artifact_id,
                                    server_event.evidence_artifact_id,
                                }
                            )
                        ),
                        {
                            "type": "http_client_server",
                            "method": client_key[0],
                            "route": client_key[1],
                        },
                    )
                )
        return output

    def _kafka_relations(
        self,
        producers: Sequence[tuple[NormalizedLogEvent, Mapping[str, Any]]],
        consumers: Sequence[tuple[NormalizedLogEvent, Mapping[str, Any]]],
        entity_keys: set[str],
    ) -> list[RelationEvidence]:
        output: list[RelationEvidence] = []
        for producer_event, producer in producers:
            for consumer_event, consumer in consumers:
                source = producer.get("source_entity")
                target = consumer.get("target_entity")
                if not self._valid_endpoints(source, target, entity_keys):
                    continue
                producer_key = self._kafka_key(producer)
                consumer_key = self._kafka_key(consumer)
                if producer_key is None or producer_key != consumer_key:
                    continue
                refs = tuple(
                    sorted(
                        {
                            producer_event.evidence_artifact_id,
                            consumer_event.evidence_artifact_id,
                        }
                    )
                )
                topic_key = f"topic:{canonical_hash({'topic': producer_key[0]})[:24]}"
                output.append(
                    RelationEvidence(
                        RelationKind.PUBLISHED_TO,
                        str(source),
                        topic_key,
                        refs,
                        {"type": "kafka_record", "topic": producer_key[0]},
                    )
                )
                output.append(
                    RelationEvidence(
                        RelationKind.CONSUMED_FROM,
                        str(target),
                        topic_key,
                        refs,
                        {"type": "kafka_record", "topic": producer_key[0]},
                    )
                )
        return output

    @staticmethod
    def _kafka_key(value: Mapping[str, Any]) -> tuple[Any, Any, Any] | None:
        identity = value.get("message_identity") or value.get("key_hash")
        key = (value.get("topic"), value.get("partition"), identity)
        return None if None in key else key

    @staticmethod
    def _valid_endpoints(source: Any, target: Any, entity_keys: set[str]) -> bool:
        return (
            isinstance(source, str)
            and isinstance(target, str)
            and source != target
            and source in entity_keys
            and target in entity_keys
        )

    @staticmethod
    def _dedupe_entities(values) -> tuple[ObservedEntityDraft, ...]:
        unique: dict[str, ObservedEntityDraft] = {}
        for value in values:
            existing = unique.get(value.stable_key)
            if existing is None:
                unique[value.stable_key] = value
                continue
            unique[value.stable_key] = ObservedEntityDraft(
                entity_kind=existing.entity_kind,
                stable_key=existing.stable_key,
                display_name=existing.display_name,
                component_snapshot_id=existing.component_snapshot_id,
                identity_status=existing.identity_status,
                provider_identity_masked=existing.provider_identity_masked,
                attributes_masked=existing.attributes_masked,
                evidence_refs=tuple(sorted(set(existing.evidence_refs) | set(value.evidence_refs))),
            )
        return tuple(unique[key] for key in sorted(unique))
