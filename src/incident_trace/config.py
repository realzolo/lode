"""Application configuration.

All settings are loaded from environment variables prefixed with ``IT_``
(for example ``IT_DATABASE_URL``). A ``.env`` file is supported and read
automatically when present.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="IT_",
        extra="ignore",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/incident_trace"

    # HTTP
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_group_id: str = "incident-trace-consumer"
    kafka_topic_pattern: str = r"alert\..*"
    kafka_dlq_topic: str = "incident_trace.dlq"
    kafka_unassigned_topic: str = "incident_trace.unassigned"

    # Security / auth
    secret_key: str = "change-me-in-production"
    # Comma-separated list of allowed CORS origins (browser fetch sources).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    jwt_ttl_seconds: int = 86400


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
