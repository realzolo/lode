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

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


# The only envelope version this consumer can correctly parse.
CONSUMER_SCHEMA_VERSION = "alert.v1"


class AlertErrorLog(BaseModel):
    """Faithful port of the lark-alert.ts ``AlertErrorLog`` structure."""

    name: str
    message: str
    stack: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    # cause may be a nested error log or an arbitrary JSON value (or null).
    cause: Any | None = None


class AlertMessage(BaseModel):
    # Locked contract: only exactly "alert.v1" is accepted; everything else DLQs.
    schema_version: Literal["alert.v1"]
    alert_id: str = Field(min_length=1)
    occurred_at: datetime
    event_type: str = Field(min_length=1)
    level: AlertLevel
    title: str = Field(min_length=1)
    dedupe_key: str = Field(min_length=1)
    dedupe_ttl_seconds: int
    fields: dict[str, Any] = Field(default_factory=dict)
    error_log: AlertErrorLog | None = None

    @property
    def level_value(self) -> str:
        return self.level.value
