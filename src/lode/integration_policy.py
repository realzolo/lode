"""Extensible integration-kind contracts.

Kinds are registered in code rather than constrained by the database. Each
registration owns validation, capabilities, secret fields, and form metadata.
The control plane and workers both use this module as their source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_RELATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


class IntegrationPolicyError(ValueError):
    pass


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatabaseIntegrationConfig(_Config):
    engine: Literal["postgresql", "mysql"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    tls: Literal[True] = True
    allowed_tables: list[str] = Field(min_length=1, max_length=100)
    sensitive_columns: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("allowed_tables")
    @classmethod
    def validate_relations(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not _RELATION.fullmatch(item) for item in value):
            raise ValueError("allowed tables must be unique, qualified base-table names")
        return value

    @model_validator(mode="after")
    def validate_mysql_database_scope(self) -> "DatabaseIntegrationConfig":
        if self.engine == "mysql" and any(
            item.split(".", 1)[0] != self.database for item in self.allowed_tables
        ):
            raise ValueError("MySQL allowed tables must belong to the configured database")
        return self


class KafkaIntegrationConfig(_Config):
    bootstrap_servers: list[str] = Field(min_length=1, max_length=20)
    sasl_mechanism: Literal["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"] = "PLAIN"
    username: str = Field(min_length=1, max_length=200)
    topics: list[str] = Field(min_length=1, max_length=50)
    consumer_groups: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("bootstrap_servers", "topics", "consumer_groups")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("integration list values must be unique")
        return value


class LokiIntegrationConfig(_Config):
    base_url: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=1000, ge=1, le=5000)


class PrometheusIntegrationConfig(_Config):
    base_url: str = Field(min_length=1, max_length=1000)
    query: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class IntegrationKindDefinition:
    key: str
    version: int
    label: str
    capabilities: frozenset[str]
    config_model: type[_Config]
    secret_fields: tuple[str, ...]
    required_secret_fields: tuple[str, ...]
    form: tuple[dict[str, Any], ...]


_KINDS = MappingProxyType(
    {
        "database": IntegrationKindDefinition(
            key="database", version=1, label="Database",
            capabilities=frozenset({"test", "query_catalog"}),
            config_model=DatabaseIntegrationConfig, secret_fields=("password",),
            required_secret_fields=("password",),
            form=(
                {"key": "engine", "input": "select", "required": True, "options": ["postgresql", "mysql"]},
                {"key": "host", "input": "text", "required": True},
                {"key": "port", "input": "number", "required": True},
                {"key": "database", "input": "text", "required": True},
                {"key": "username", "input": "text", "required": True},
                {"key": "allowed_tables", "input": "string-list", "required": True},
                {"key": "sensitive_columns", "input": "string-list", "required": False},
                {"key": "password", "input": "password", "required": True, "secret": True},
            ),
        ),
        "kafka": IntegrationKindDefinition(
            key="kafka", version=1, label="Kafka",
            capabilities=frozenset({"test", "snapshot"}),
            config_model=KafkaIntegrationConfig, secret_fields=("password",),
            required_secret_fields=("password",),
            form=(
                {"key": "bootstrap_servers", "input": "string-list", "required": True},
                {"key": "sasl_mechanism", "input": "select", "required": True, "options": ["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"]},
                {"key": "username", "input": "text", "required": True},
                {"key": "topics", "input": "string-list", "required": True},
                {"key": "consumer_groups", "input": "string-list", "required": False},
                {"key": "password", "input": "password", "required": True, "secret": True},
            ),
        ),
        "loki": IntegrationKindDefinition(
            key="loki", version=1, label="Grafana Loki",
            capabilities=frozenset({"test", "log_search"}),
            config_model=LokiIntegrationConfig, secret_fields=("bearer_token",),
            required_secret_fields=(),
            form=(
                {"key": "base_url", "input": "text", "required": True},
                {"key": "limit", "input": "number", "required": True},
                {"key": "bearer_token", "input": "password", "required": False, "secret": True},
            ),
        ),
        "prometheus": IntegrationKindDefinition(
            key="prometheus", version=1, label="Prometheus",
            capabilities=frozenset({"test", "snapshot"}),
            config_model=PrometheusIntegrationConfig, secret_fields=("bearer_token",),
            required_secret_fields=(),
            form=(
                {"key": "base_url", "input": "text", "required": True},
                {"key": "query", "input": "text", "required": True},
                {"key": "bearer_token", "input": "password", "required": False, "secret": True},
            ),
        ),
    }
)


def integration_kinds() -> tuple[IntegrationKindDefinition, ...]:
    return tuple(_KINDS.values())


def integration_kind(kind: str) -> IntegrationKindDefinition:
    try:
        return _KINDS[kind]
    except KeyError as exc:
        raise IntegrationPolicyError(f"unsupported integration kind '{kind}'") from exc


def _reject_env_reference(value: Any) -> None:
    if isinstance(value, str) and value.strip().lower().startswith("env:/"):
        raise IntegrationPolicyError("indirect environment secret references are not supported")
    if isinstance(value, dict):
        for item in value.values():
            _reject_env_reference(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_env_reference(item)


def _bootstrap_host(value: str) -> str:
    host, separator, port = value.rpartition(":")
    if not separator or not host or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise IntegrationPolicyError("Kafka bootstrap servers must be DNS-host:port")
    return host


def normalize_integration_config(kind: str, value: dict[str, Any]) -> dict[str, Any]:
    _reject_env_reference(value)
    definition = integration_kind(kind)
    data = definition.config_model.model_validate(value).model_dump(mode="json")
    if "bootstrap_servers" in data:
        for item in data["bootstrap_servers"]:
            _bootstrap_host(item)
    elif "base_url" in data:
        parsed = urlsplit(data["base_url"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise IntegrationPolicyError("HTTP integration endpoints must be credential-free HTTPS URLs")
        data["base_url"] = data["base_url"].rstrip("/")
    return data


def normalize_integration_secrets(kind: str, value: dict[str, Any]) -> dict[str, str]:
    _reject_env_reference(value)
    definition = integration_kind(kind)
    supplied = set(value)
    allowed = set(definition.secret_fields)
    required = set(definition.required_secret_fields)
    if not required.issubset(supplied) or not supplied.issubset(allowed):
        raise IntegrationPolicyError(
            "integration secret fields do not match the registered kind contract"
        )
    normalized = {key: str(item) for key, item in value.items()}
    if any(not item or len(item) > 8_000 for item in normalized.values()):
        raise IntegrationPolicyError("integration secrets must be non-empty and at most 8000 characters")
    return normalized
