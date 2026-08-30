from __future__ import annotations

from prometheus_client import generate_latest

import lode.metrics  # noqa: F401 - importing registers the complete metric surface


def test_required_operational_metrics_are_registered() -> None:
    exposition = generate_latest().decode("utf-8")
    required = {
        "lode_active_workspaces",
        "lode_consumer_heartbeat_unixtime",
        "lode_consumer_messages_total",
        "lode_dead_letters_total",
        "lode_investigation_job_queue_depth",
        "lode_investigation_job_claim_duration_seconds",
        "lode_investigation_lease_recoveries_total",
        "lode_investigation_duration_seconds",
        "lode_investigation_operation_duration_seconds",
        "lode_investigation_operation_failures_total",
        "lode_investigation_decision_cost_total",
        "lode_native_read_candidates_total",
        "lode_evidence_queries_total",
        "lode_evidence_result_bytes",
        "lode_evidence_provider_scanned_rows_total",
        "lode_evidence_query_cost_total",
        "lode_evidence_access_stage_duration_seconds",
        "lode_resource_events_total",
        "lode_identity_resolutions_total",
        "lode_resource_invalidation_duration_seconds",
        "lode_source_resolution_total",
        "lode_source_snapshot_incompatible_total",
        "lode_ai_protocol_total",
        "lode_model_routing_total",
        "lode_model_capacity_gaps_total",
        "lode_model_context_utilization_ratio",
        "lode_model_context_compression_ratio",
        "lode_model_tokens_total",
        "lode_model_cost_total",
        "lode_verifier_outcomes_total",
        "lode_sse_connections",
        "lode_sse_replay_lag_events",
    }

    missing = sorted(name for name in required if f"# HELP {name} " not in exposition)
    assert missing == []
