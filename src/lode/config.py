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

    # Security / auth
    # Required (no default): a missing signing key must fail fast rather than
    # silently fall back to a known placeholder that would forge verifiable tokens.
    secret_key: str
    # Comma-separated list of allowed CORS origins (browser fetch sources).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    jwt_ttl_seconds: int = 86400

    # Embeddings (opt-in semantic shared memory / M5)
    # Leave ``embedding_api_key_ref`` empty to disable semantic matching; the
    # runner then falls back to exact trigger_signature matching. The key is a
    # ``env://NAME`` reference (or a literal) resolved by ``resolve_api_key``.
    embedding_base_url: str = "https://api.openai.com/v1/embeddings"
    embedding_api_key_ref: str = ""
    embedding_model: str = "text-embedding-3-small"
    # Cosine distance at or below which a retrieved memory is considered a match
    # (0 = identical direction, 2 = opposite). 0.25 ≙ similarity ≥ 0.75.
    embedding_threshold: float = 0.25


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
