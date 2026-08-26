"""Strict incident alert contract shared with business applications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


CONSUMER_SCHEMA_VERSION = "incident.alert.v1"


class AlertError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=20_000)
    stack: str = Field(max_length=50_000)
    cause: Any


class AlertCorrelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str | None = Field(default=None, min_length=1, max_length=500)
    job_id: str | None = Field(default=None, min_length=1, max_length=500)
    delivery_id: str | None = Field(default=None, min_length=1, max_length=500)
    provider_transaction_id: str | None = Field(default=None, min_length=1, max_length=500)


class AlertMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["incident.alert.v1"]
    alert_id: str = Field(min_length=1, max_length=500)
    occurred_at: datetime
    severity: AlertSeverity
    event: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$", max_length=500)
    service_name: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
    environment: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    request_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    correlation: AlertCorrelation = Field(default_factory=AlertCorrelation)
    error: AlertError

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @property
    def level_value(self) -> str:
        return self.severity.value


@dataclass(frozen=True)
class NormalizedAlertError:
    name: str
    message: str
    stack: str
    cause: Any
    properties: dict[str, Any]


def normalize_alert_error(message: AlertMessage) -> NormalizedAlertError:
    error = message.error
    return NormalizedAlertError(
        name=error.type,
        message=error.message,
        stack=error.stack,
        cause=error.cause,
        properties={},
    )
