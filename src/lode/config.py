"""Application configuration.

All settings are loaded from environment variables prefixed with ``LODE_``
(for example ``LODE_DATABASE_URL``). A ``.env`` file is supported and read
automatically when present.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal

from aiokafka.helpers import create_ssl_context
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` against the project root rather than the current working
# directory. This file lives at `src/lode/config.py`, so the root is
# three levels up. Making the path absolute means settings load correctly no
# matter where the process is launched from (one-off scripts, `make serve`,
# the test runner, cron, etc.) — previously a non-project CWD caused `.env` to
# be missed and the required master key to fail validation.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_prefix="LODE_",
        extra="forbid",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/lode"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    # Local and compatibility deployments may use plaintext transports.
    # Internet-facing brokers should prefer certificate-verified SASL_SSL.
    kafka_security_protocol: Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"] = (
        "PLAINTEXT"
    )
    kafka_sasl_mechanism: Literal["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"] = "PLAIN"
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    kafka_ssl_ca_file: str = ""
    # Different investigations may run concurrently. Each investigation itself
    # owns one strictly serial action lane.
    worker_concurrency: int = 5
    # Deployment-selected location for disposable, pinned source checkouts.
    evidence_git_cache_dir: str = "/tmp/lode/git"

    # Security / auth. One deployment-provided root is expanded into distinct
    # domain-separated JWT, encryption, and evidence-authorization keys.
    master_key: str
    # Independent authentication boundary for the isolated command runner.
    # API and consumer processes must not receive this key.
    command_runner_url: str = "http://command-runner:8080"
    command_runner_key: str = ""

    @model_validator(mode="after")
    def validate_keys(self):
        configured = {"LODE_MASTER_KEY": self.master_key}
        if self.command_runner_key:
            configured["LODE_COMMAND_RUNNER_KEY"] = self.command_runner_key
        invalid = [name for name, value in configured.items() if len(value.encode()) < 32]
        if invalid:
            raise ValueError(f"security keys must contain at least 32 bytes: {sorted(invalid)}")
        if len(configured.values()) != len(set(configured.values())):
            raise ValueError("security keys must be independent values")
        has_username = bool(self.kafka_sasl_username)
        has_password = bool(self.kafka_sasl_password)
        uses_sasl = self.kafka_security_protocol.startswith("SASL_")
        uses_tls = self.kafka_security_protocol in {"SSL", "SASL_SSL"}
        if uses_sasl and not (has_username and has_password):
            raise ValueError("Kafka SASL requires both username and password")
        if not uses_sasl and (has_username or has_password):
            raise ValueError("Kafka credentials require a SASL security protocol")
        if self.kafka_ssl_ca_file and not uses_tls:
            raise ValueError("Kafka CA configuration requires an SSL security protocol")
        return self

    def _derive_key(self, purpose: bytes) -> str:
        material = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"lode/key-derivation/v1",
            info=purpose,
        ).derive(self.master_key.encode("utf-8"))
        return base64.urlsafe_b64encode(material).decode("ascii")

    @property
    def jwt_signing_key(self) -> str:
        return self._derive_key(b"jwt-signing")

    @property
    def data_encryption_key(self) -> str:
        return self._derive_key(b"data-encryption")

    @property
    def evidence_authorization_key(self) -> str:
        return self._derive_key(b"evidence-authorization")

    @property
    def credential_identity_key(self) -> str:
        return self._derive_key(b"credential-identity")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def kafka_security_kwargs(configured: Settings | None = None) -> dict:
    """Build the validated, certificate-verifying aiokafka security boundary."""

    configured = configured or settings
    if configured.kafka_security_protocol == "PLAINTEXT":
        return {}
    result: dict = {"security_protocol": configured.kafka_security_protocol}
    if configured.kafka_security_protocol in {"SSL", "SASL_SSL"}:
        result["ssl_context"] = create_ssl_context(
            cafile=configured.kafka_ssl_ca_file or None
        )
    if configured.kafka_security_protocol in {"SASL_PLAINTEXT", "SASL_SSL"}:
        result.update(
            sasl_mechanism=configured.kafka_sasl_mechanism,
            sasl_plain_username=configured.kafka_sasl_username,
            sasl_plain_password=configured.kafka_sasl_password,
        )
    return result
