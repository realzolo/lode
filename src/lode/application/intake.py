"""Strict Kafka and ergonomic manual incident intake contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lode.masking import mask_structure

EVENT_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
SOURCE_REVISION_PATTERN = r"^[0-9a-f]{40}$"
MAX_CAUSE_DEPTH = 16
_WHITESPACE = re.compile(r"\s+")


class IncidentError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=500)
    message: str = Field(max_length=20_000)
    stack: str = Field(max_length=50_000)
    cause: IncidentError | None


class KafkaIncidentAlert(BaseModel):
    """The deployed ``incident.alert.v1`` wire contract. Do not extend it."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["incident.alert.v1"]
    alert_id: str = Field(min_length=1, max_length=500)
    occurred_at: datetime
    severity: Literal["CRITICAL", "WARNING"]
    event: str = Field(min_length=1, max_length=500, pattern=EVENT_PATTERN)
    trace_id: str
    source_revision: str = Field(pattern=SOURCE_REVISION_PATTERN)
    error: IncidentError

    @field_validator("occurred_at", mode="before")
    @classmethod
    def timestamp_must_be_a_json_string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("occurred_at must be an RFC 3339 string")
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


class ManualIncidentRequest(BaseModel):
    """Minimal human report; deliberately independent from the Kafka shape."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["manual-incident.v1"]
    summary: str = Field(min_length=1, max_length=2_000)
    error_text: str = Field(min_length=1, max_length=50_000)
    trace_id: str | None = Field(default=None, min_length=1, max_length=500)
    repository_binding_id: int | None = Field(default=None, gt=0)

    @field_validator("summary", "error_text", "trace_id")
    @classmethod
    def text_must_be_trimmed(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("text values must be trimmed")
        return value


@dataclass(frozen=True, slots=True)
class NormalizedSignal:
    schema_version: Literal["incident-signal.v1"]
    source_type: Literal["kafka", "manual"]
    source_event_id: str | None
    idempotency_key_hash: str | None
    signal_kind: Literal["firing", "recovered"]
    observed_at: datetime
    severity: Literal["CRITICAL", "WARNING", "UNCLASSIFIED"]
    title: str
    summary: str
    repository_binding_id: int | None
    trace_id: str | None
    source_revision: str | None
    fingerprint: str
    error_masked: dict[str, Any]
    raw_payload_masked: dict[str, Any]
    masking_categories: tuple[str, ...]
    sealed_payload: str


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def mask_failure_payload(value: Any) -> tuple[Any, tuple[str, ...]]:
    """Mask an untrusted pre-validation payload without exposing an opaque trace."""

    candidate = dict(value) if isinstance(value, dict) else value
    if isinstance(candidate, dict) and "trace_id" in candidate:
        candidate["trace_id"] = "<VALUE_REF:incident.trace_id>"
    return mask_structure(candidate)


def normalize_kafka(message: KafkaIncidentAlert) -> NormalizedSignal:
    error_payload = message.error.model_dump(mode="json")
    error_masked, error_categories = mask_structure(error_payload)
    raw_payload = message.model_dump(mode="json")
    raw_payload["trace_id"] = "<VALUE_REF:incident.trace_id>"
    raw_masked, raw_categories = mask_structure(raw_payload)
    return NormalizedSignal(
        schema_version="incident-signal.v1",
        source_type="kafka",
        source_event_id=message.alert_id,
        idempotency_key_hash=None,
        signal_kind="firing",
        observed_at=message.occurred_at,
        severity=message.severity,
        title=message.event,
        summary=message.error.message,
        repository_binding_id=None,
        trace_id=message.trace_id,
        source_revision=message.source_revision,
        fingerprint=canonical_hash(
            {
                "source_type": "kafka",
                "event": message.event,
                "error_type": message.error.type,
                "source_revision": message.source_revision,
            }
        ),
        error_masked=error_masked,
        raw_payload_masked=raw_masked,
        masking_categories=tuple(sorted(set(error_categories) | set(raw_categories))),
        sealed_payload=json.dumps(
            message.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        ),
    )


def normalize_manual(
    message: ManualIncidentRequest,
    *,
    idempotency_key: str,
    observed_at: datetime | None = None,
) -> NormalizedSignal:
    received_at = observed_at or datetime.now(UTC)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("observed_at must include timezone")
    normalized_error = _WHITESPACE.sub(" ", message.error_text).strip()
    error_masked_value, error_categories = mask_structure({"text": message.error_text})
    raw_payload = message.model_dump(mode="json")
    if message.trace_id is not None:
        raw_payload["trace_id"] = "<VALUE_REF:incident.trace_id>"
    raw_masked, raw_categories = mask_structure(raw_payload)
    return NormalizedSignal(
        schema_version="incident-signal.v1",
        source_type="manual",
        source_event_id=None,
        idempotency_key_hash=canonical_hash(idempotency_key),
        signal_kind="firing",
        observed_at=received_at.astimezone(UTC),
        severity="UNCLASSIFIED",
        title=message.summary,
        summary=message.summary,
        repository_binding_id=message.repository_binding_id,
        trace_id=message.trace_id,
        source_revision=None,
        fingerprint=canonical_hash(
            {
                "source_type": "manual",
                "repository_binding_id": message.repository_binding_id,
                "error_text": normalized_error,
            }
        ),
        error_masked=error_masked_value,
        raw_payload_masked=raw_masked,
        masking_categories=tuple(sorted(set(error_categories) | set(raw_categories))),
        sealed_payload=json.dumps(
            message.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        ),
    )
