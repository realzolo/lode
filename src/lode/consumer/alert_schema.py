"""Validation of the locked Kafka alert message format (spec alert.v1).

This mirrors *exactly* the ``KafkaAlertMessage`` envelope emitted by the
business ``lark-alert.ts`` utility. There are no backward-compatibility shims:
the contract is fixed to ``alert.v1`` and every field below is accepted exactly
as the producer sends it — nothing more, nothing less.

Schema-version policy (strict, no backward-compat shims):
  * The contract is locked to exactly ``alert.v1``. ``schema_version`` must be
    the literal string ``"alert.v1"``. Any other value (the old ``1.1``
    envelope, a future ``alert.v2`` the consumer has not been taught to parse,
    or a breaking contract) is rejected by the ``Literal`` type and routed to
    the DLQ.
  * A producer that evolves the envelope must ship a coordinated consumer change
    first. Fail-fast rejection keeps an incompatible contract from silently
    corrupting an analysis — there is no silent "warn and continue" path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertLevel(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


# The only envelope version this consumer can correctly parse.
CONSUMER_SCHEMA_VERSION = "alert.v1"


class AlertErrorLog(BaseModel):
    """Faithful port of the lark-alert.ts ``AlertErrorLog`` structure."""

    model_config = ConfigDict(extra="forbid")

    name: str
    message: str
    stack: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    # cause may be a nested error log or an arbitrary JSON value (or null).
    cause: Any | None = None


class AlertMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Locked contract: only exactly "alert.v1" is accepted; everything else DLQs.
    schema_version: Literal["alert.v1"]
    alert_id: str = Field(min_length=1)
    occurred_at: datetime
    event_type: str = Field(min_length=1)
    level: AlertLevel
    title: str = Field(min_length=1)
    dedupe_key: str = Field(min_length=1)
    dedupe_ttl_seconds: int = Field(gt=0)
    version: str = Field(min_length=1)
    git_commit: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    error_log: AlertErrorLog | None = None

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @property
    def level_value(self) -> str:
        return self.level.value


@dataclass(frozen=True)
class NormalizedAlertError:
    """Lossless wire error plus its most useful structured failure contract."""

    name: str
    message: str
    stack: str | None
    cause: Any
    properties: dict[str, Any]


_GENERIC_ERROR_NAMES = {"object", "string", "number", "boolean", "undefined", "null"}


def normalize_alert_error(message: AlertMessage) -> NormalizedAlertError:
    """Normalize real errors and the producer's serialized non-Error values."""

    error = message.error_log
    if error is None:
        fallback = next(
            (
                value.strip()
                for key in ("error", "reason", "message", "detail")
                if isinstance((value := message.fields.get(key)), str) and value.strip()
            ),
            "",
        )
        return NormalizedAlertError(
            name="Error",
            message=fallback,
            stack=None,
            cause=None,
            properties={"contract": {}, "wire_error": None},
        )

    parsed_message: Any = None
    try:
        parsed_message = json.loads(error.message)
    except (json.JSONDecodeError, TypeError):
        pass

    property_value = error.properties.get("value")
    contract = property_value if isinstance(property_value, (dict, list)) else parsed_message
    if not isinstance(contract, (dict, list)):
        contract = {}

    code = None
    contract_message = None
    if isinstance(contract, dict):
        code = contract.get("code") or contract.get("errorCode") or contract.get("error_code")
        contract_message = contract.get("message") or contract.get("error") or contract.get("reason")
    code = code or message.fields.get("gatewayCode") or message.fields.get("errorCode")
    contract_message = contract_message or message.fields.get("gatewayMessage")
    normalized_name = error.name.strip() or "Error"
    if normalized_name.lower() in _GENERIC_ERROR_NAMES and isinstance(code, str) and code.strip():
        normalized_name = code.strip()
    normalized_message = (
        contract_message.strip()
        if isinstance(contract_message, str) and contract_message.strip()
        else error.message.strip()
    )
    return NormalizedAlertError(
        name=normalized_name,
        message=normalized_message,
        stack=error.stack,
        cause=error.cause,
        properties={
            **error.properties,
            "contract": contract,
            "wire_error": {"name": error.name, "message": error.message},
        },
    )
