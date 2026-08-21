"""Validation of the locked Kafka alert message format (spec v1.1).

Only the fields exposed by the business ``lark-alert.ts`` utility are accepted.
Required: schema_version ("1.1"), level, title, env, timestamp.
Optional: eventType, project, fields.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


class AlertMessage(BaseModel):
    schema_version: str = Field(pattern=r"^1\.1$")
    level: AlertLevel
    title: str = Field(min_length=1)
    env: str = Field(min_length=1)
    timestamp: datetime
    event_type: Optional[str] = None
    project: Optional[str] = None
    fields: dict[str, Any] = Field(default_factory=dict)

    @property
    def level_value(self) -> str:
        return self.level.value
