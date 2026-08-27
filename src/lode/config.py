"""Application configuration.

All settings are loaded from environment variables prefixed with ``LODE_``
(for example ``LODE_DATABASE_URL``). A ``.env`` file is supported and read
automatically when present.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

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
    # SASL/PLAIN authentication. Defaults are the unauthenticated local-dev
    # profile (PLAINTEXT, no credentials). For a secured broker set
    # ``kafka_security_protocol=SASL_PLAINTEXT`` and supply the PLAIN username
    # and password. When ``kafka_sasl_username`` is empty, no SASL handshake is
    # attempted even if a security protocol is set.
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = "PLAIN"
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
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
