from __future__ import annotations

import json
from types import SimpleNamespace

from lode.db.models import EvidenceArtifact, EvidenceAssertion
from lode.infrastructure.evidence_archive import (
    _event_attributes,
    _replace_sealed_value,
    _scope_coverage,
    _trace_correlation_claim,
)
from lode.infrastructure.model_evidence import model_evidence_package


def test_loki_scope_coverage_and_payssion_event_fields_are_preserved() -> None:
    snapshot = SimpleNamespace(
        scope_config={
            "root_filter_dnf": [
                [
                    {
                        "label": "app",
                        "operator": "any_of",
                        "values": ["pornbox", "payment-gateway", "sonakit"],
                    }
                ]
            ]
        }
    )
    result = {
        "records": [
            {
                "labels": {"app": "payment-gateway"},
                "value": (
                    "Payssion createOrder pm_id=enets_sg result_code=405 event=provider_rejected"
                ),
            }
        ],
        "record_count": 1,
        "truncated": False,
    }

    coverage = _scope_coverage(snapshot, result)
    attributes = _event_attributes(result["records"][0]["value"])

    assert coverage["allowed_apps"] == ["payment-gateway", "pornbox", "sonakit"]
    assert coverage["returned_apps"] == ["payment-gateway"]
    assert coverage["scopes_without_hits"] == ["pornbox", "sonakit"]
    assert coverage["truncated"] is False
    assert attributes["pm_id"] == "enets_sg"
    assert attributes["result_code"] == "405"
    assert attributes["event"] == "provider_rejected"


def test_trace_claim_and_model_package_never_expose_trace_or_enumerable_hash() -> None:
    raw_trace = "trace-production-215272664893440"
    redacted = _replace_sealed_value(
        {"records": [{"value": f"request trace={raw_trace}"}]},
        raw_trace,
        "<SEALED:incident.trace_id>",
    )
    coverage = {
        "allowed_apps": ["payment-gateway", "pornbox", "sonakit"],
        "returned_apps": ["payment-gateway"],
        "scopes_without_hits": ["pornbox", "sonakit"],
        "truncated": False,
        "record_count": 1,
    }
    claim = _trace_correlation_claim(
        connector_snapshot_id=7,
        candidate_id=8,
        access_decision_id=9,
        full_scope_discovery=True,
        coverage=coverage,
    )
    artifact = EvidenceArtifact(
        id=101,
        investigation_id=1,
        collection_id=None,
        artifact_kind="normalized_log_result",
        evidence_class="runtime",
        content_masked=redacted,
        content_hash="a" * 64,
        provenance={"scope_coverage": coverage},
        source_revision=None,
        data_class="masked",
        prompt_injection_markers=[],
    )
    assertion = EvidenceAssertion(
        id=102,
        investigation_id=1,
        assertion_kind="fact",
        status="confirmed",
        statement="Loki matched the sealed incident trace.",
        structured_claim=claim,
        supporting_evidence_refs=[101],
        counter_evidence_refs=[],
        missing_validation=[],
        assertion_hash="b" * 64,
    )

    package = model_evidence_package(artifact, (assertion,))
    rendered = json.dumps(package, ensure_ascii=False, sort_keys=True)

    assert package["content"] == redacted
    assert package["provenance"]["scope_coverage"] == coverage
    assert package["server_assertions"][0]["structured_claim"] == claim
    assert raw_trace not in rendered
    assert "value_hash" not in rendered
    assert claim["query_scope"] == "connector_root"
