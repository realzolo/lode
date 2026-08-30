from __future__ import annotations

import pytest
from pydantic import ValidationError

from lode.application.context_compaction import context_summary_json_schema
from lode.application.model_planner import decision_json_schema
from lode.application.native_query import native_query_json_schema
from lode.application.reporting import (
    ConfigurationAssessmentPayload,
    report_json_schema,
    verification_json_schema,
)
from lode.structured_output import (
    StrictResponseSchemaError,
    parse_json_document,
    protocol_health_json_schema,
    validate_strict_response_schema,
)


@pytest.mark.parametrize(
    "schema_factory",
    [
        decision_json_schema,
        native_query_json_schema,
        report_json_schema,
        verification_json_schema,
        context_summary_json_schema,
        protocol_health_json_schema,
    ],
)
def test_every_production_response_schema_uses_the_closed_strict_subset(
    schema_factory,
) -> None:
    validate_strict_response_schema(schema_factory())


def test_planner_schema_requires_at_least_one_hypothesis() -> None:
    schema = decision_json_schema()

    assert schema["properties"]["hypotheses"]["minItems"] == 1


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (
            {
                "type": "object",
                "properties": {"optional": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
            "require every declared property",
        ),
        (
            {
                "type": "object",
                "properties": {"open": {"type": "object", "additionalProperties": True}},
                "required": ["open"],
                "additionalProperties": False,
            },
            "open object",
        ),
        (
            {
                "type": "object",
                "properties": {"anything": {}},
                "required": ["anything"],
                "additionalProperties": False,
            },
            "unconstrained",
        ),
    ],
)
def test_invalid_strict_response_schemas_fail_before_provider_io(
    schema: dict, message: str
) -> None:
    with pytest.raises(StrictResponseSchemaError, match=message):
        validate_strict_response_schema(schema)


def test_dynamic_json_documents_reject_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        parse_json_document('{"status":503,"status":200}')


def test_configuration_documents_decode_only_from_the_v2_wire_contract() -> None:
    payload = ConfigurationAssessmentPayload(
        scope="payments.timeout",
        declared_value_json='{"seconds":30}',
        runtime_value_json="null",
        effective_status="unknown",
        evidence_refs=(),
    )

    assert payload.declared_value == {"seconds": 30}
    assert payload.runtime_value is None
    with pytest.raises(ValidationError):
        ConfigurationAssessmentPayload.model_validate(
            {
                "scope": "payments.timeout",
                "declared_value": 30,
                "runtime_value": None,
                "effective_status": "unknown",
                "evidence_refs": [],
            }
        )


def test_dynamic_json_documents_reject_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        parse_json_document('{"latency":NaN}')
