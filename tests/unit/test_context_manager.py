from __future__ import annotations

import pytest

from lode.application.context import ContextCapacityExceeded, ContextManager, ExactJSONTokenizer
from lode.domain.model_execution import ContextEvidence
from lode.domain.types import ModelRole

TOKENIZER = ExactJSONTokenizer()


def evidence(
    artifact_id: int,
    content: dict,
    *,
    pinned: bool = False,
    counter: bool = False,
    relevance: float = 1.0,
) -> ContextEvidence:
    return ContextEvidence(
        artifact_id=artifact_id,
        artifact_kind="normalized_log_result",
        content=content,
        token_count=TOKENIZER.count_json(content),
        relevance=relevance,
        pinned=pinned,
        counter_evidence=counter,
    )


def test_role_context_removes_hidden_model_state_and_deduplicates_evidence() -> None:
    item = evidence(1, {"fact": "runtime timeout"}, pinned=True)
    context = ContextManager().build(
        role=ModelRole.VERIFIER,
        state_packet={
            "hypotheses": [{"id": "h1"}],
            "hidden_reasoning": "do not transfer",
            "nested": {"scratchpad": "private", "fact": "public"},
        },
        evidence=(item, item),
        tokenizer=TOKENIZER,
        allowed_input_tokens=10_000,
        reserved_output_tokens=1_000,
        provider_safety_margin_tokens=100,
    )

    assert context.evidence_refs == (1,)
    assert "hidden_reasoning" not in context.state_packet
    assert "scratchpad" not in context.state_packet["nested"]


def test_counter_evidence_is_selected_before_equal_optional_support() -> None:
    support = evidence(1, {"kind": "support"}, relevance=1)
    counter = evidence(2, {"kind": "counter"}, counter=True, relevance=1)
    capacity = TOKENIZER.count_json({}) + counter.token_count

    context = ContextManager().build(
        role=ModelRole.PLANNER,
        state_packet={},
        evidence=(support, counter),
        tokenizer=TOKENIZER,
        allowed_input_tokens=capacity,
        reserved_output_tokens=100,
        provider_safety_margin_tokens=10,
    )

    assert context.evidence_refs == (2,)


def test_pinned_context_never_silently_truncates() -> None:
    with pytest.raises(ContextCapacityExceeded):
        ContextManager().build(
            role=ModelRole.SYNTHESIZER,
            state_packet={},
            evidence=(evidence(1, {"large": "x" * 100}, pinned=True),),
            tokenizer=TOKENIZER,
            allowed_input_tokens=10,
            reserved_output_tokens=100,
            provider_safety_margin_tokens=10,
        )


def test_declared_token_count_must_match_selected_tokenizer() -> None:
    invalid = ContextEvidence(
        artifact_id=1,
        artifact_kind="source_file",
        content={"path": "src/app.py"},
        token_count=1,
        relevance=1,
    )
    with pytest.raises(ValueError, match="token count"):
        ContextManager().build(
            role=ModelRole.PLANNER,
            state_packet={},
            evidence=(invalid,),
            tokenizer=TOKENIZER,
            allowed_input_tokens=1_000,
            reserved_output_tokens=100,
            provider_safety_margin_tokens=10,
        )
