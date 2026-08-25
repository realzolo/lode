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
    # Active application bindings are the consumer subscription source of truth.
    # The consumer polls this interval so control-plane state changes do not
    # require a process restart.
    kafka_subscription_refresh_seconds: float = 5.0
    # A runtime heartbeat older than this is reported as an error to operators.
    kafka_runtime_stale_seconds: float = 20.0
    kafka_topic_validation_timeout_seconds: float = 10.0
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
    # Max records fetched per `getmany` poll. Bounds memory use and redelivery blast
    # radius; messages are still committed one-at-a-time so the at-least-once +
    # idempotent contract is unchanged.
    kafka_batch_max_records: int = 100
    # Different investigations may run concurrently. Each investigation itself
    # owns one strictly serial action lane.
    engine_concurrency: int = 5
    investigation_max_evidence_steps: int = 12
    investigation_max_model_calls: int = 10
    investigation_timeout_seconds: int = 600

    # Worker lease: how long a claimed job is reserved before another worker may
    # reclaim it after a crash. Heartbeats extend it while a job is running.
    worker_lease_ttl_seconds: int = 300
    # Worker poll loop idle wait between empty claim attempts.
    worker_poll_interval_seconds: float = 1.0
    # Max attempts before a job is declared dead (no further automatic retry).
    job_max_attempts: int = 5
    # Base delay (seconds) for exponential backoff between retries.
    job_base_retry_delay: float = 5.0
    # Investigation evidence is collected in a bounded interval around the
    # alert's occurrence. The window is persisted with each run and never
    # recomputed after the fact.
    investigation_window_before_seconds: int = 900
    investigation_window_after_seconds: int = 900
    # Evidence gateway (M2): bounds on the read-only Git source inspection.
    # Each investigation receives a fresh temporary sandbox beneath this directory;
    # its read-only, ref-pinned clones are removed after excerpts are persisted.
    # The caps guarantee a single incident can never exhaust disk or blow up the
    # prompt with an entire monorepo.
    evidence_git_cache_dir: str = "/tmp/lode/git"
    evidence_git_max_files: int = 8
    evidence_git_max_bytes: int = 80_000
    evidence_git_snippet_lines: int = 12
    evidence_git_clone_timeout_seconds: int = 60
    # Administrator-controlled repository context files. These are the only
    # instruction/document files that can enter immutable source evidence.
    evidence_git_context_paths: str = "AGENTS.md,AGENT.md,CLAUDE.md,README.md,README.*,.github/AGENTS.md"
    evidence_git_context_max_files: int = 8
    evidence_git_context_max_bytes: int = 80_000
    # Read-only DB proxy hardening (M3). TLS certificate and hostname
    # verification is mandatory for every data source; it has no bypass.
    # The server-side lock timeout (seconds) applied to every proxied query so a
    # contended table lock cannot hang the read-only connection.
    db_proxy_lock_timeout_seconds: float = 3.0

    # External service collectors are disabled until each DNS endpoint is
    # explicitly allowed. The deployment network policy must enforce the same
    # list (for example through an egress gateway) to prevent DNS rebinding.
    integration_egress_allowlist: str = ""
    integration_collect_timeout_seconds: float = 12.0

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

    # Rate limiting (M6 hardening)
    # In-process fixed-window limiter applied to every non-exempt route. The
    # limit is generous by default so normal usage (and the test suite) is never
    # throttled; lower it for production abuse protection. Set
    # ``rate_limit_enabled`` to false to disable entirely.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 600

    # LLM resilience (T9): bounded exponential-backoff retries on transient
    # errors (network blips, provider 5xx). 4xx are never retried. After retries
    # are exhausted, the investigation records an unavailable result.
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 0.5
    # Final synthesis is materially slower than a health probe. Keep transport
    # timeouts distinct so a healthy reasoning model is not rejected by the
    # probe path or cut off by the former hard-coded 30-second request limit.
    llm_request_timeout_seconds: float = 120.0
    llm_probe_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 8_192

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
