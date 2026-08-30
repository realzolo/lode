from __future__ import annotations

import json

from lode.application.context_compaction import (
    ContextSummaryPayload,
    ContextSummaryValidator,
)
from lode.domain.model_execution import ContextEvidence


def evidence(artifact_id: int, *, counter: bool = False, pinned: bool = False) -> ContextEvidence:
    return ContextEvidence(
        artifact_id=artifact_id,
        artifact_kind="log_event",
        content={"status": 503, "revision": "a" * 40},
        token_count=10,
        relevance=1,
        pinned=pinned,
        counter_evidence=counter,
    )


def test_summary_retains_counter_evidence_and_stable_scalars() -> None:
    values = (evidence(1), evidence(2, counter=True))
    payload = ContextSummaryPayload(
        summary_json=json.dumps({"status": 503, "revision": "a" * 40}),
        input_evidence_refs=(1, 2),
        covered_claim_refs=(1,),
        retained_counter_evidence_refs=(2,),
        omitted_evidence_refs=(1,),
    )

    result = ContextSummaryValidator().validate(payload, values)

    assert result.valid


def test_summary_rejects_counter_evidence_loss_and_scalar_drift() -> None:
    values = (evidence(1), evidence(2, counter=True))
    payload = ContextSummaryPayload(
        summary_json=json.dumps({"status": 200, "revision": "b" * 40}),
        input_evidence_refs=(1, 2),
        covered_claim_refs=(1,),
        retained_counter_evidence_refs=(),
        omitted_evidence_refs=(2,),
    )

    result = ContextSummaryValidator().validate(payload, values)

    assert not result.valid
    assert "counter_evidence_not_retained" in result.codes
    assert "summary_stable_scalar_drift" in result.codes


def test_pinned_evidence_cannot_enter_a_summary() -> None:
    payload = ContextSummaryPayload(
        summary_json=json.dumps({"status": 503}),
        input_evidence_refs=(1,),
        covered_claim_refs=(1,),
        retained_counter_evidence_refs=(),
        omitted_evidence_refs=(),
    )

    result = ContextSummaryValidator().validate(payload, (evidence(1, pinned=True),))

    assert result.codes == ("pinned_evidence_must_not_be_summarized",)
