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

OPERATION_DURATION = Histogram(
    "lode_investigation_operation_duration_seconds",
    "Investigation operation duration by server operation kind and terminal status",
    ["operation_kind", "status"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
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
