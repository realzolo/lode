"""Prometheus metrics for the Lode platform.

Single-process exposition on the default registry. For multi-worker / gunicorn
deployments enable prometheus-client multiprocess mode (set
``PROMETHEUS_MULTIPROC_DIR`` and switch to a per-worker ``CollectorRegistry``)
before scaling horizontally — see the prometheus_client docs. The instruments
below are the production minimum for an incident-analysis service: Kafka intake
volume, dead-letter pressure, analysis throughput, engine-fallback rate, and
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

# --- Dead letters -----------------------------------------------------------
# kind: dlq | unassigned
DEAD_LETTERS = Counter(
    "lode_dead_letters_total",
    "Dead letters recorded, by kind",
    ["kind"],
)

# --- Analysis engine --------------------------------------------------------
# result: scheduled | completed | failed | heuristic
ANALYSES = Counter(
    "lode_analyses_total",
    "Analysis runs, labelled by result",
    ["result"],
)

# Current in-flight analyses inside this worker (the Semaphore-bound runners).
# A gauge, not a counter, because it goes up and down with concurrency.
ENGINE_IN_FLIGHT = Gauge(
    "lode_analyses_in_flight",
    "Analyses currently executing inside this worker",
)

# --- LLM --------------------------------------------------------------------
# outcome: success | fallback
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
