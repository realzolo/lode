"""Incident contracts and shared Kafka/manual normalization use case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lode.masking import mask_structure

EVENT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
SOURCE_REVISION_PATTERN = r"^[0-9a-f]{40}$"
MAX_CAUSE_DEPTH = 16


class IncidentError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=500)
    message: str = Field(max_length=20_000)
    stack: str = Field(max_length=50_000)
    cause: IncidentError | None


class KafkaIncidentAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["incident.alert.v2"]
    source_event_id: str = Field(min_length=1, max_length=500)
    dedup_key: str = Field(min_length=1, max_length=500)
    event_kind: Literal["firing", "recovered"]
    occurred_at: datetime
    severity: Literal["CRITICAL", "WARNING"]
    event: str = Field(min_length=1, max_length=500, pattern=EVENT_PATTERN)
    component: str = Field(min_length=1, max_length=500, pattern=EVENT_PATTERN)
    environment: str = Field(min_length=1, max_length=100, pattern=EVENT_PATTERN)
    trace_id: str | None = None
    source_revision: str | None = Field(default=None, pattern=SOURCE_REVISION_PATTERN)
    error: IncidentError | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def timestamp_must_be_a_json_string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise TypeError("occurred_at must be an RFC 3339 string")
        return value

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("error")
    @classmethod
    def cause_depth_is_bounded(cls, value: IncidentError) -> IncidentError:
        depth = 1
        current = value.cause
        while current is not None:
            depth += 1
            if depth > MAX_CAUSE_DEPTH:
                raise ValueError(f"error cause depth exceeds {MAX_CAUSE_DEPTH}")
            current = current.cause
        return value

    @model_validator(mode="after")
    def firing_events_require_a_failure(self) -> KafkaIncidentAlert:
        if self.event_kind == "firing" and self.error is None:
            raise ValueError("firing events require an error payload")
        return self


class ManualAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["log", "trace", "dependency_response", "runtime_config"]
    label: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=20_000)


class ManualIncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: int = Field(gt=0)
    dedup_key: str = Field(min_length=1, max_length=500)
    event_kind: Literal["firing", "recovered"] = "firing"
    occurred_at: datetime
    severity: Literal["CRITICAL", "WARNING"] = "WARNING"
    event: str = Field(min_length=1, max_length=500, pattern=EVENT_PATTERN)
    component: str = Field(min_length=1, max_length=500, pattern=EVENT_PATTERN)
    environment: str = Field(min_length=1, max_length=100, pattern=EVENT_PATTERN)
    trace_id: str | None = None
    source_revision: str | None = Field(default=None, pattern=SOURCE_REVISION_PATTERN)
    error: IncidentError | None = None
    attachments: list[ManualAttachment] = Field(default_factory=list, max_length=10)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def timestamp_must_be_a_json_string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise TypeError("occurred_at must be an RFC 3339 string")
        return value

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("error")
    @classmethod
    def cause_depth_is_bounded(cls, value: IncidentError | None) -> IncidentError | None:
        if value is None:
            return None
        return KafkaIncidentAlert.cause_depth_is_bounded(value)

    @model_validator(mode="after")
    def firing_events_require_a_failure(self) -> ManualIncidentRequest:
        if self.event_kind == "firing" and self.error is None:
            raise ValueError("firing events require an error payload")
        return self


@dataclass(frozen=True, slots=True)
class NormalizedIncident:
    source_type: Literal["kafka", "manual"]
    source_event_id: str | None
    dedup_key: str
    event_kind: Literal["firing", "recovered"]
    occurred_at: datetime
    severity: Literal["CRITICAL", "WARNING"]
    event: str
    component: str
    environment: str
    trace_id: str | None
    source_revision: str | None
    error_masked: dict[str, Any]
    raw_payload_masked: dict[str, Any]
    attachments_masked: tuple[dict[str, Any], ...]
    masking_categories: tuple[str, ...]


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def mask_failure_payload(value: Any) -> tuple[Any, tuple[str, ...]]:
    """Mask an untrusted pre-validation payload without exposing an opaque trace."""

    candidate = dict(value) if isinstance(value, dict) else value
    if isinstance(candidate, dict) and "trace_id" in candidate:
        candidate["trace_id"] = "<VALUE_REF:incident.trace_id>"
    return mask_structure(candidate)


def _normalize(
    *,
    source_type: Literal["kafka", "manual"],
    source_event_id: str | None,
    dedup_key: str,
    event_kind: Literal["firing", "recovered"],
    occurred_at: datetime,
    severity: Literal["CRITICAL", "WARNING"],
    event: str,
    component: str,
    environment: str,
    trace_id: str | None,
    source_revision: str | None,
    error: IncidentError | None,
    raw_payload: dict[str, Any],
    attachments: list[ManualAttachment],
) -> NormalizedIncident:
    error_masked, error_categories = mask_structure(
        {} if error is None else error.model_dump(mode="json")
    )
    raw_for_masking = dict(raw_payload)
    if "trace_id" in raw_for_masking:
        raw_for_masking["trace_id"] = (
            "<VALUE_REF:incident.trace_id>" if trace_id is not None else None
        )
    raw_masked, raw_categories = mask_structure(raw_for_masking)
    attachment_rows: list[dict[str, Any]] = []
    categories = set(error_categories) | set(raw_categories)
    for attachment in attachments:
        masked, found = mask_structure(attachment.model_dump(mode="json"))
        categories.update(found)
        attachment_rows.append(masked)
    return NormalizedIncident(
        source_type=source_type,
        source_event_id=source_event_id,
        dedup_key=dedup_key,
        event_kind=event_kind,
        occurred_at=occurred_at,
        severity=severity,
        event=event,
        component=component,
        environment=environment,
        trace_id=trace_id,
        source_revision=source_revision,
        error_masked=error_masked,
        raw_payload_masked=raw_masked,
        attachments_masked=tuple(attachment_rows),
        masking_categories=tuple(sorted(categories)),
    )


def normalize_kafka(message: KafkaIncidentAlert) -> NormalizedIncident:
    return _normalize(
        source_type="kafka",
        source_event_id=message.source_event_id,
        dedup_key=message.dedup_key,
        event_kind=message.event_kind,
        occurred_at=message.occurred_at,
        severity=message.severity,
        event=message.event,
        component=message.component,
        environment=message.environment,
        trace_id=message.trace_id,
        source_revision=message.source_revision,
        error=message.error,
        raw_payload=message.model_dump(mode="json"),
        attachments=[],
    )


def normalize_manual(message: ManualIncidentRequest) -> NormalizedIncident:
    return _normalize(
        source_type="manual",
        source_event_id=None,
        dedup_key=message.dedup_key,
        event_kind=message.event_kind,
        occurred_at=message.occurred_at,
        severity=message.severity,
        event=message.event,
        component=message.component,
        environment=message.environment,
        trace_id=message.trace_id,
        source_revision=message.source_revision,
        error=message.error,
        raw_payload=message.model_dump(mode="json", exclude={"workspace_id", "attachments"}),
        attachments=message.attachments,
    )
