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
    # SASL/PLAIN authentication. Defaults are the unauthenticated local-dev
    # profile (PLAINTEXT, no credentials). For a secured broker set
    # ``kafka_security_protocol=SASL_PLAINTEXT`` and supply the PLAIN username
    # and password. When ``kafka_sasl_username`` is empty, no SASL handshake is
    # attempted even if a security protocol is set.
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = "PLAIN"
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    # Max analyses allowed to run concurrently inside one worker process.
    # Caps DB-connection and LLM-provider pressure during redelivery bursts.
    engine_concurrency: int = 5

    # Worker lease: how long a claimed job is reserved before another worker may
    # reclaim it after a crash. Heartbeats extend it while a job is running.
    worker_lease_ttl_seconds: int = 300
    # Worker poll loop idle wait between empty claim attempts.
    worker_poll_interval_seconds: float = 1.0
    # Max attempts before a job is declared dead (no further automatic retry).
    job_max_attempts: int = 5
    # Base delay (seconds) for exponential backoff between retries.
    job_base_retry_delay: float = 5.0
    # Semantic version of the analysis engine; stamped on each analysis run so
    # conclusions can be reproduced/attributed to a specific engine behaviour.
    engine_version: str = "1.0"

    # Evidence gateway (M2): bounds on the read-only Git source inspection.
    # The clone is read-only and pinned to a fixed ref (alert commit or the
    # repo's default branch); these caps guarantee a single incident can never
    # exhaust disk or blow up the prompt with an entire monorepo.
    evidence_git_cache_dir: str = "/var/cache/lode/git"
    evidence_git_max_files: int = 20
    evidence_git_max_bytes: int = 200_000
    evidence_git_snippet_lines: int = 12
    evidence_git_clone_timeout_seconds: int = 60
    # Evidence retention (M3): how many days an EvidenceArtifact stays valid
    # before the startup reaper hard-deletes it. 0 disables expiry (keep forever).
    evidence_retention_days: int = 90

    # Read-only DB proxy hardening (M3)
    # When true, any *structured* (host-based) data source without ``sslmode`` in
    # {require, verify-full} is rejected so a cross-network link to a production
    # replica cannot downgrade to cleartext. Leave false for local dev.
    db_proxy_require_tls: bool = False
    # Server-side lock timeout (seconds) applied to every proxied query so a
    # contended table lock cannot hang the read-only connection.
    db_proxy_lock_timeout_seconds: float = 3.0

    # Security / auth
    # Required (no default): a missing signing key must fail fast rather than
    # silently fall back to a known placeholder that would forge verifiable tokens.
    # This key signs JWTs (the *auth* signing key). Data-source credential
    # encryption uses a separate key — see ``data_encryption_key_ref``.
    secret_key: str
    # Comma-separated list of allowed CORS origins (browser fetch sources).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    jwt_ttl_seconds: int = 86400
    # Data-encryption key for at-rest secrets (data-source passwords). Separation
    # of duties: the auth signing key (``secret_key``) MUST NOT also encrypt
    # data, so a JWT-signing key leak cannot decrypt stored credentials. This is
    # strictly an ``env://NAME`` reference (never a plaintext literal); the
    # referenced value is derived into a Fernet key. When empty, encryption
    # derives the key from ``secret_key`` for backward compatibility with data
    # already encrypted under the legacy scheme — new deployments should set this.
    data_encryption_key_ref: str = ""

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


def kafka_security_kwargs() -> dict:
    """Build SASL/security kwargs for aiokafka clients from the current settings.

    Returns an empty dict when no username is configured, so the default
    (unauthenticated PLAINTEXT) is used for local development. When a username
    is present the security protocol and PLAIN credentials are returned for
    aiokafka to perform the SASL handshake.
    """
    if not settings.kafka_sasl_username:
        return {}
    return {
        "security_protocol": settings.kafka_security_protocol,
        "sasl_mechanism": settings.kafka_sasl_mechanism,
        "sasl_plain_username": settings.kafka_sasl_username,
        "sasl_plain_password": settings.kafka_sasl_password,
    }
