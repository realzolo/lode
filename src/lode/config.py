"""Application configuration.

All settings are loaded from environment variables prefixed with ``LODE_``
(for example ``LODE_DATABASE_URL``). A ``.env`` file is supported and read
automatically when present.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` against the project root rather than the current working
# directory. This file lives at `src/lode/config.py`, so the root is
# three levels up. Making the path absolute means settings load correctly no
# matter where the process is launched from (one-off scripts, `make serve`,
# the test runner, cron, etc.) — previously a non-project CWD caused `.env` to
# be missed and `secret_key` (a required field) to fail validation.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_prefix="LODE_",
        extra="ignore",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/lode"

    # HTTP
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_group_id: str = "lode-consumer"
    kafka_topic_pattern: str = r"alert\..*"
    kafka_dlq_topic: str = "lode.dlq"
    kafka_unassigned_topic: str = "lode.unassigned"
    # Max analyses allowed to run concurrently inside one consumer process.
    # Caps DB-connection and LLM-provider pressure during redelivery bursts.
    engine_concurrency: int = 5

    # Security / auth
    # Required (no default): a missing signing key must fail fast rather than
    # silently fall back to a known placeholder that would forge verifiable tokens.
    secret_key: str
    # Comma-separated list of allowed CORS origins (browser fetch sources).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    jwt_ttl_seconds: int = 86400

    # Embeddings (opt-in semantic shared memory / M5)
    # Leave ``embedding_api_key_ref`` empty to disable semantic matching; the
    # runner then falls back to exact trigger_signature matching. The key is
    # resolved by ``resolve_api_key`` and MUST be an ``env://NAME`` reference or
    # an encrypted token (never a plaintext literal): ``resolve_api_key`` is
    # strict and fails closed on a non-token value.
    embedding_base_url: str = "https://api.openai.com/v1/embeddings"
    embedding_api_key_ref: str = ""
    embedding_model: str = "text-embedding-3-small"
    # Cosine distance at or below which a retrieved memory is considered a match
    # (0 = identical direction, 2 = opposite). 0.25 ≙ similarity ≥ 0.75.
    embedding_threshold: float = 0.25
    # Semantic search backend for shared memory. ``"python"`` (default) ranks
    # candidates in-process with ``cosine_distance`` and works against any
    # PostgreSQL with no extension. ``"pgvector"`` offloads distance computation
    # (and ANN search with an HNSW index) to the database via the ``<=>``
    # operator, casting the stored ``real[]`` column to ``vector`` at query time.
    # Requires the ``pgvector`` extension on the host; if that extension is
    # missing the search transparently falls back to the Python backend, so the
    # feature never hard-breaks. No column-type migration is required either way
    # — the portable ``real[]`` storage is preserved.
    embedding_backend: str = "python"

    # Rate limiting (M6 hardening)
    # In-memory fixed-window limiter applied to every non-exempt route. The
    # limit is generous by default so normal usage (and the test suite) is never
    # throttled; lower it for production abuse protection. Set
    # ``rate_limit_enabled`` to false to disable entirely.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 600

    # LLM resilience (T9): bounded exponential-backoff retries on transient
    # errors (network blips, provider 5xx). 4xx are never retried. After the
    # retries are exhausted the runner degrades to the deterministic heuristic.
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 0.5

    # Shared-memory aging (T8): reusable conclusions (the ``memories`` table) are
    # given this many days of validity when written. Expired memories are no
    # longer returned by retrieval and are reaped at startup. Set to 0 to disable
    # expiry (keep memories forever).
    memory_ttl_days: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
