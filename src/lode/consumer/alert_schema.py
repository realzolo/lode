"""Validation of the locked Kafka alert message format (spec v1.x).

Only the fields exposed by the business ``lark-alert.ts`` utility are accepted.
Required: schema_version (a "1.x" string), level, title, env, timestamp.
Optional: eventType, project, fields.

Schema-version policy (T10, backward compatible):
  * The contract is locked to the ``1.x`` major line. ``schema_version`` must
    match ``^1\\.\\d+$`` — so ``1.1`` (current), ``1.2`` (new optional fields
    added by ``lark-alert``), etc. are all accepted. This lets the producer
    evolve the alert envelope without a coordinated consumer rollout.
  * Anything outside ``1.x`` (e.g. ``2.0``, a breaking change) is rejected by
    the pattern and routed to the DLQ, so an incompatible contract never
    silently corrupts an analysis.
  * A version newer than the consumer's understood ``1.1`` is still processed,
    but logged as a warning so operators notice the producer has moved ahead.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


# The consumer understands 1.1; the minor line is forward-tolerant within 1.x.
CONSUMER_SCHEMA_VERSION = "1.1"


class AlertMessage(BaseModel):
    # Accept any 1.x envelope; reject breaking (2.x+) versions so they DLQ.
    schema_version: str = Field(pattern=r"^1\.\d+$")
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
