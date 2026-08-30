from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lode.application.evidence_graph import EvidenceGraphProjector, FrozenIdentityAlias
from lode.domain.investigation import NormalizedLogEvent
from lode.domain.types import RelationKind

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


def event(
    position: str,
    artifact_id: int,
    *,
    occurred_at: datetime = NOW,
    identity: str = "api",
    relation_hints=(),
    assertion_id: int = 99,
):
    return NormalizedLogEvent(
        occurred_at=occurred_at,
        connector_snapshot_id=7,
        provider_position=position,
        raw_excerpt_masked="masked log",
        attributes_masked={},
        resource_attributes_masked={},
        trace_match={"assertion_id": assertion_id, "location": "body"},
        component_candidates=({"identity": identity, "location": "resource.service"},),
        relation_hints=tuple(relation_hints),
        revision_hints=(),
        provider_metadata={"loki.stream": "stdout"},
        evidence_artifact_id=artifact_id,
    )


ALIASES = (
    FrozenIdentityAlias("api", 10, "component:api", "API", "verified"),
    FrozenIdentityAlias("worker", 11, "component:worker", "Worker", "verified"),
)


def test_timeline_is_deterministic_and_shared_trace_has_no_direction() -> None:
    projection = EvidenceGraphProjector().project(
        (
            event("b", 2, occurred_at=NOW + timedelta(seconds=1), identity="worker"),
            event("a", 1),
        ),
        aliases=ALIASES,
    )

    assert [value.provider_position for value in projection.timeline] == ["a", "b"]
    assert {value.stable_key for value in projection.entities} == {
        "component:api",
        "component:worker",
    }
    assert projection.relations == ()


def test_span_and_http_pair_create_only_evidence_backed_called_relations() -> None:
    span = event(
        "span",
        1,
        relation_hints=(
            {
                "type": "span_parent",
                "parent_entity": "component:api",
                "child_entity": "component:worker",
                "parent_span_id": "parent",
            },
        ),
    )
    client = event(
        "client",
        2,
        identity="api",
        relation_hints=(
            {
                "type": "http_client",
                "source_entity": "component:api",
                "method": "POST",
                "route": "/jobs",
            },
        ),
    )
    server = event(
        "server",
        3,
        identity="worker",
        occurred_at=NOW + timedelta(milliseconds=20),
        relation_hints=(
            {
                "type": "http_server",
                "target_entity": "component:worker",
                "method": "POST",
                "route": "/jobs",
            },
        ),
    )

    projection = EvidenceGraphProjector().project((server, client, span), aliases=ALIASES)
    called = [value for value in projection.relations if value.kind is RelationKind.CALLED]

    assert len(called) == 1
    assert called[0].source_stable_key == "component:api"
    assert called[0].target_stable_key == "component:worker"
    assert called[0].evidence_refs == (1, 2, 3)


def test_kafka_alignment_creates_topic_entity_and_two_directed_edges() -> None:
    producer = event(
        "producer",
        1,
        relation_hints=(
            {
                "type": "kafka_producer",
                "source_entity": "component:api",
                "topic": "jobs",
                "partition": 2,
                "message_identity": "m-1",
            },
        ),
    )
    consumer = event(
        "consumer",
        2,
        identity="worker",
        relation_hints=(
            {
                "type": "kafka_consumer",
                "target_entity": "component:worker",
                "topic": "jobs",
                "partition": 2,
                "message_identity": "m-1",
            },
        ),
    )

    projection = EvidenceGraphProjector().project((consumer, producer), aliases=ALIASES)

    topic = next(value for value in projection.entities if value.entity_kind == "topic")
    assert topic.display_name == "jobs"
    assert {value.kind for value in projection.relations} == {
        RelationKind.PUBLISHED_TO,
        RelationKind.CONSUMED_FROM,
    }
    assert all(value.target_stable_key == topic.stable_key for value in projection.relations)


def test_unknown_and_ambiguous_identity_emit_observations_without_snapshot_promotion() -> None:
    aliases = (
        *ALIASES,
        FrozenIdentityAlias("shared", 12, "component:a", "A", "ambiguous"),
        FrozenIdentityAlias("shared", 13, "component:b", "B", "ambiguous"),
    )
    projection = EvidenceGraphProjector().project(
        (event("unknown", 1, identity="new-service"), event("ambiguous", 2, identity="shared")),
        aliases=aliases,
    )

    unknown = [value for value in projection.entities if value.entity_kind == "unknown_component"]
    assert {value.identity_status for value in unknown} == {"unknown", "ambiguous"}
    assert all(value.component_snapshot_id is None for value in unknown)
    assert len(projection.resource_observations) == 2
    assert {
        value.structured_payload["identity_status"] for value in projection.resource_observations
    } == {
        "unknown",
        "ambiguous",
    }
