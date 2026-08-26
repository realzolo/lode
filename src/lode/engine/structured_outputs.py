"""Strict model-output contracts for the investigation engine."""

from __future__ import annotations

from lode.engine.llm import ResponseSchema


def _object(properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _array(items: dict) -> dict:
    return {"type": "array", "items": items}


def _nullable(value: dict) -> dict:
    return {"anyOf": [value, {"type": "null"}]}


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
STRING_LIST = _array(STRING)
INTEGER_LIST = _array(INTEGER)

DECISION_RESPONSE_SCHEMA = ResponseSchema(
    name="investigation_next_action",
    schema=_object(
        {
            "action_id": STRING,
            "rationale": STRING,
            "hypothesis": _object(
                {
                    "mechanism": STRING,
                    "contract_violation": STRING,
                    "trigger": STRING,
                    "propagation": STRING,
                    "missing_evidence": STRING,
                }
            ),
        }
    ),
)

CODE_VERDICT_RESPONSE_SCHEMA = ResponseSchema(
    name="code_causal_verdicts",
    schema=_object(
        {
            "verdicts": _array(
                _object(
                    {
                        "artifact_id": INTEGER,
                        "verified": {"type": "boolean"},
                        "reason": STRING,
                    }
                )
            )
        }
    ),
)

CODE_FINDING_SCHEMA = _object(
    {
        "status": {"type": "string", "enum": ["confirmed", "hypothesis", "no_defect", "not_found"]},
        "artifact_id": _nullable(INTEGER),
        "repo_id": _nullable(INTEGER),
        "revision": _nullable(STRING),
        "revision_role": _nullable({"type": "string", "enum": ["incident", "latest"]}),
        "path": _nullable(STRING),
        "symbol": _nullable(STRING),
        "start_line": _nullable(INTEGER),
        "end_line": _nullable(INTEGER),
        "issue_type": _nullable(STRING),
        "faulty_behavior": STRING,
        "why_wrong": STRING,
        "expected_behavior": STRING,
        "trigger_condition": STRING,
        "causal_chain": STRING_LIST,
        "incident_evidence_refs": INTEGER_LIST,
        "supporting_evidence_refs": INTEGER_LIST,
        "counter_evidence_refs": INTEGER_LIST,
        "missing_validation": STRING_LIST,
        "fix_direction": STRING,
        "test_scenario": STRING,
    }
)

FACT_SCHEMA = _object(
    {
        "text": STRING,
        "rationale": STRING,
        "evidence_refs": INTEGER_LIST,
    }
)

REPORT_RESPONSE_SCHEMA = ResponseSchema(
    name="investigation_report_v1",
    schema=_object(
        {
            "result_state": {
                "type": "string",
                "enum": ["confirmed", "hypothesis", "insufficient", "unavailable"],
            },
            "headline": STRING,
            "summary": STRING,
            "incident_cause": _object(
                {
                    "status": {"type": "string", "enum": ["confirmed", "hypothesis", "not_found"]},
                    "mechanism": STRING,
                    "why": STRING,
                    "causal_chain": STRING_LIST,
                    "evidence_refs": INTEGER_LIST,
                }
            ),
            "code_diagnosis": _object(
                {
                    "status": {
                        "type": "string",
                        "enum": ["confirmed", "hypothesis", "no_defect", "not_found"],
                    },
                    "summary": STRING,
                    "findings": _array(CODE_FINDING_SCHEMA),
                }
            ),
            "confirmed_facts": _array(FACT_SCHEMA),
            "counter_evidence": _array(FACT_SCHEMA),
            "evidence_gaps": STRING_LIST,
            "next_step": _object({"type": STRING, "text": STRING}),
        }
    ),
)
