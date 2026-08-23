"""Validation of the locked Kafka alert message format (spec v1.1).

Only the fields exposed by the business ``lark-alert.ts`` utility are accepted.
Required: schema_version (exactly "1.1"), level, title, env, timestamp.
Optional: eventType, project, fields.

Schema-version policy (strict, no backward-compat shims):
  * The contract is locked to exactly ``1.1``. ``schema_version`` must match
    ``^1\\.1$``. Any other value (older ``1.0``, a future ``1.2`` the consumer
    has not been taught to parse, or a breaking ``2.0``) is rejected by the
    pattern and routed to the DLQ.
  * A producer that evolves the envelope must ship a coordinated consumer change
    first. Fail-fast rejection keeps an incompatible contract from silently
    corrupting an analysis — there is no silent "warn and continue" path.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


# The only envelope version this consumer can correctly parse.
CONSUMER_SCHEMA_VERSION = "1.1"


class AlertMessage(BaseModel):
    # Locked contract: only exactly 1.1 is accepted; everything else DLQs.
    schema_version: str = Field(pattern=r"^1\.1$")
    level: AlertLevel
    title: str = Field(min_length=1)
    env: str = Field(min_length=1)
    timestamp: datetime
    event_type: str | None = None
    project: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)

    @property
    def level_value(self) -> str:
        return self.level.value
