"""Prometheus metrics for the Lode platform.

Single-process exposition on the default registry. For multi-worker / gunicorn
deployments enable prometheus-client multiprocess mode (set
``PROMETHEUS_MULTIPROC_DIR`` and switch to a per-worker ``CollectorRegistry``)
before scaling horizontally — see the prometheus_client docs. The instruments
below are the production minimum for an incident-investigation service: Kafka intake
volume, dead-letter pressure, investigation throughput, model availability, and
LLM latency.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# --- Kafka intake -----------------------------------------------------------
# outcome: persisted | duplicate | dlq | unassigned
MESSAGES_RECEIVED = Counter(
    "lode_consumer_messages_total",
    "Alert messages received by the consumer, labelled by intake outcome",
    ["outcome"],
)

# Highest offset lag observed per assigned partition. A suddenly rising value
# means the consumer is falling behind (or a rebalance/stall). Gauge, not
# counter, because it tracks a position.
CONSUMER_LAG = Gauge(
    "lode_consumer_lag",
    "Kafka consumer lag (high-water-mark minus committed offset) per partition",
    ["topic", "partition"],
)

ACTIVE_WORKSPACES = Gauge(
    "lode_active_workspaces",
    "Workspaces currently assigned to this consumer process",
)

CONSUMER_HEARTBEAT = Gauge(
    "lode_consumer_heartbeat_unixtime",
    "Unix timestamp of the most recent successful consumer poll",
)

# --- Dead letters -----------------------------------------------------------
# kind: dlq | unassigned
DEAD_LETTERS = Counter(
    "lode_dead_letters_total",
    "Dead letters recorded, by kind",
    ["kind"],
)

# --- Investigation engine --------------------------------------------------
# result: scheduled | completed | failed
INVESTIGATIONS = Counter(
    "lode_investigations_total",
    "Investigation runs, labelled by result",
    ["result"],
)

# Current in-flight investigations inside this worker.
# A gauge, not a counter, because it goes up and down with concurrency.
ENGINE_IN_FLIGHT = Gauge(
    "lode_investigations_in_flight",
    "Investigations currently executing inside this worker",
)

JOB_QUEUE_DEPTH = Gauge(
    "lode_investigation_job_queue_depth",
    "Investigation jobs currently eligible to be claimed",
)

JOB_CLAIM_LATENCY = Histogram(
    "lode_investigation_job_claim_duration_seconds",
    "Duration of a durable investigation job claim attempt",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0),
)

LEASE_RECOVERIES = Counter(
    "lode_investigation_lease_recoveries_total",
    "Expired investigation leases recovered by workers",
)

INVESTIGATION_DURATION = Histogram(
    "lode_investigation_duration_seconds",
    "End-to-end worker handler duration by terminal result",
    ["result"],
    buckets=(1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

DECISION_POLICY = Counter(
    "lode_decision_policy_total",
    "Investigation decision policy outcomes and stable decision codes",
    ["outcome", "code"],
)

CONNECTOR_SELECTION = Counter(
    "lode_connector_selection_total",
    "Native connector selections and planner decisions requiring zero external calls",
    ["outcome"],
)

DECISION_COST = Counter(
    "lode_investigation_decision_cost_total",
    "Decision-policy operation cost by estimate or provider-reported actual value",
    ["kind"],
)

OPERATION_DURATION = Histogram(
    "lode_investigation_operation_duration_seconds",
    "Investigation operation duration by server operation kind and terminal status",
    ["operation_kind", "status"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

OPERATION_FAILURES = Counter(
    "lode_investigation_operation_failures_total",
    "Operation failures by stable operation kind and failure code",
    ["operation_kind", "failure_code"],
)

NATIVE_CANDIDATES = Counter(
    "lode_native_read_candidates_total",
    "Native-read authorization outcomes by language and stable rejection code",
    ["language", "outcome", "reason"],
)

EVIDENCE_QUERIES = Counter(
    "lode_evidence_queries_total",
    "Authorized evidence query attempts by adapter and outcome",
    ["adapter", "outcome"],
)

EVIDENCE_RESULT_BYTES = Histogram(
    "lode_evidence_result_bytes",
    "Normalized evidence result size by adapter",
    ["adapter"],
    buckets=(128, 512, 2_048, 8_192, 32_768, 131_072, 524_288, 2_097_152),
)

PROVIDER_SCANNED_ROWS = Counter(
    "lode_evidence_provider_scanned_rows_total",
    "Provider-reported rows scanned by adapter",
    ["adapter"],
)

EVIDENCE_QUERY_COST = Counter(
    "lode_evidence_query_cost_total",
    "Provider-reported evidence query cost by adapter",
    ["adapter"],
)

EVIDENCE_ACCESS_STAGE_LATENCY = Histogram(
    "lode_evidence_access_stage_duration_seconds",
    "Evidence policy, preflight, and execution latency",
    ["stage", "outcome"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

RESOURCE_EVENTS = Counter(
    "lode_resource_events_total",
    "Resource observation and graph publication events",
    ["kind", "outcome"],
)

IDENTITY_RESOLUTIONS = Counter(
    "lode_identity_resolutions_total",
    "Identity resolution outcomes",
    ["status"],
)

RESOURCE_INVALIDATION_LATENCY = Histogram(
    "lode_resource_invalidation_duration_seconds",
    "Resource graph invalidation latency",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0),
)

SOURCE_RESOLUTION = Counter(
    "lode_source_resolution_total",
    "Runtime source resolution outcomes",
    ["status"],
)

SOURCE_INCOMPATIBILITY = Counter(
    "lode_source_snapshot_incompatible_total",
    "Explicit runtime revision conflicts and high-specificity source incompatibilities",
)

# --- LLM --------------------------------------------------------------------
# outcome: success | unavailable
LLM_CALLS = Counter(
    "lode_llm_calls_total",
    "LLM completion attempts, labelled by outcome",
    ["outcome"],
)

LLM_LATENCY = Histogram(
    "lode_llm_latency_seconds",
    "LLM completion latency in seconds (network call only)",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0),
)

AI_PROTOCOL = Counter(
    "lode_ai_protocol_total",
    "AI invocation protocol outcomes including schema failures",
    ["outcome"],
)

MODEL_ROUTING = Counter(
    "lode_model_routing_total",
    "Model routing outcomes by role, execution class, and provider",
    ["role", "execution_class", "provider", "outcome"],
)

MODEL_CAPACITY_GAPS = Counter(
    "lode_model_capacity_gaps_total",
    "Tasks for which no frozen model binding satisfies policy",
    ["role", "execution_class"],
)

MODEL_CONTEXT_UTILIZATION = Histogram(
    "lode_model_context_utilization_ratio",
    "Exact selected context tokens divided by the admitted input budget",
    ["role", "execution_class"],
    buckets=(0.1, 0.25, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0),
)

MODEL_COMPRESSION_RATIO = Histogram(
    "lode_model_context_compression_ratio",
    "Validated context summary output tokens divided by input tokens",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0),
)

MODEL_TOKENS = Counter(
    "lode_model_tokens_total",
    "Model tokens by role, execution class, provider, and direction",
    ["role", "execution_class", "provider", "direction"],
)

MODEL_COST = Counter(
    "lode_model_cost_total",
    "Model cost by role, execution class, provider, and estimate/actual kind",
    ["role", "execution_class", "provider", "kind"],
)

VERIFIER_OUTCOMES = Counter(
    "lode_verifier_outcomes_total",
    "Independent verifier outcomes",
    ["outcome"],
)

SSE_CONNECTIONS = Gauge(
    "lode_sse_connections",
    "Active investigation event streams",
)

SSE_REPLAY_LAG = Histogram(
    "lode_sse_replay_lag_events",
    "Events between the requested SSE cursor and canonical state",
    buckets=(0, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1_000),
)
