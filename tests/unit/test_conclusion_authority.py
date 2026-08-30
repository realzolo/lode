from __future__ import annotations

from lode.application.conclusion_validation import ConclusionValidator
from lode.application.source_authority import (
    ConfigurationAuthorityEngine,
    SourceAuthorityEngine,
)

SHA = "a" * 40


def report() -> dict:
    return {
        "result_state": "confirmed",
        "incident_cause": {
            "status": "confirmed",
            "mechanism": "application_code",
            "evidence_refs": [7],
        },
        "code_diagnosis": {
            "status": "confirmed",
            "summary": "branch defect",
            "finding_refs": [11],
        },
    }


def test_alert_revision_permits_confirmed_code_without_git_resolution() -> None:
    assessment = SourceAuthorityEngine().assess(
        repository_snapshot_id=1,
        revision_origin="alert_revision",
        requested_ref=SHA,
        resolved_sha=SHA,
        incident_source_revision=SHA,
    )

    result = ConclusionValidator().validate(
        report(),
        source_assessments=(assessment,),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert assessment.permits_confirmed_code
    assert result.result_state == "confirmed"


def test_bound_branch_head_is_authoritative_for_secondary_repository_code() -> None:
    assessment = SourceAuthorityEngine().assess(
        repository_snapshot_id=1,
        revision_origin="bound_branch_head",
        requested_ref="main",
        resolved_sha=SHA,
        incident_source_revision=SHA,
    )

    result = ConclusionValidator().validate(
        report(),
        source_assessments=(assessment,),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert assessment.permits_confirmed_code
    assert result.result_state == "confirmed"


def test_runtime_revision_conflict_revokes_code_confirmation() -> None:
    assessment = SourceAuthorityEngine().assess(
        repository_snapshot_id=1,
        revision_origin="alert_revision",
        requested_ref=SHA,
        resolved_sha=SHA,
        incident_source_revision=SHA,
        contradiction_evidence_refs=(4,),
    )
    result = ConclusionValidator().validate(
        report(),
        source_assessments=(assessment,),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert assessment.authority_status == "contradicted"
    assert "source_snapshot_incompatible" in result.reasons


def test_unavailable_bound_branch_head_does_not_confirm_code() -> None:
    assessment = SourceAuthorityEngine().assess(
        repository_snapshot_id=1,
        revision_origin="bound_branch_head",
        requested_ref="main",
        resolved_sha=None,
        incident_source_revision=SHA,
    )

    assert assessment.authority_status == "unavailable"
    assert not assessment.permits_confirmed_code
    assert assessment.mismatch_reasons == ("source_revision_unavailable",)


def test_declared_configuration_without_runtime_evidence_is_not_effective() -> None:
    config = ConfigurationAuthorityEngine().assess(
        scope="deployment.timeout",
        declared_value=30,
        runtime_value=None,
    )
    source = SourceAuthorityEngine().assess(
        repository_snapshot_id=1,
        revision_origin="alert_revision",
        requested_ref=SHA,
        resolved_sha=SHA,
        incident_source_revision=SHA,
    )
    value = report()
    value["incident_cause"]["mechanism"] = "configuration"
    result = ConclusionValidator().validate(
        value,
        source_assessments=(source,),
        configuration_assessments=(config,),
        verifier_status="approved",
    )

    assert config.status == "unknown"
    assert "runtime_configuration_not_corroborated" in result.reasons


def test_configuration_cause_without_any_assessment_is_not_confirmed() -> None:
    value = report()
    value["incident_cause"]["mechanism"] = "configuration"
    value["code_diagnosis"]["status"] = "not_found"

    result = ConclusionValidator().validate(
        value,
        source_assessments=(),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert result.result_state == "hypothesis"
    assert "runtime_configuration_not_corroborated" in result.reasons


def test_confirmed_external_cause_does_not_require_source_authority() -> None:
    value = report()
    value["incident_cause"]["mechanism"] = "external_dependency"
    value["code_diagnosis"]["status"] = "not_found"

    result = ConclusionValidator().validate(
        value,
        source_assessments=(),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert result.result_state == "confirmed"


def test_incompatible_code_snapshot_does_not_downgrade_external_cause() -> None:
    source = SourceAuthorityEngine().assess(
        repository_snapshot_id=2,
        revision_origin="bound_branch_head",
        requested_ref="main",
        resolved_sha=SHA,
        incident_source_revision=SHA,
        contradiction_evidence_refs=(9,),
        contradiction_reasons=("source_snapshot_incompatible",),
    )
    value = report()
    value["incident_cause"]["mechanism"] = "external_dependency"
    value["code_diagnosis"]["status"] = "confirmed"

    result = ConclusionValidator().validate(
        value,
        source_assessments=(source,),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert source.compatibility_status == "incompatible"
    assert result.result_state == "confirmed"
    assert result.code_status == "hypothesis"
    assert result.report["code_diagnosis"]["status"] == "hypothesis"


def test_confirmed_report_without_a_confirmation_anchor_is_downgraded() -> None:
    value = report()
    value["incident_cause"]["status"] = "hypothesis"
    value["code_diagnosis"]["status"] = "not_found"

    result = ConclusionValidator().validate(
        value,
        source_assessments=(),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert result.result_state == "hypothesis"
    assert "confirmed_conclusion_anchor_missing" in result.reasons


def test_confirmed_incident_cause_without_evidence_is_downgraded() -> None:
    value = report()
    value["incident_cause"]["mechanism"] = "external_dependency"
    value["incident_cause"]["evidence_refs"] = []
    value["code_diagnosis"]["status"] = "not_found"

    result = ConclusionValidator().validate(
        value,
        source_assessments=(),
        configuration_assessments=(),
        verifier_status="approved",
    )

    assert result.result_state == "hypothesis"
    assert "confirmed_incident_evidence_missing" in result.reasons


def test_verifier_failure_downgrades_an_otherwise_confirmed_report() -> None:
    source = SourceAuthorityEngine().assess(
        repository_snapshot_id=1,
        revision_origin="alert_revision",
        requested_ref=SHA,
        resolved_sha=SHA,
        incident_source_revision=SHA,
    )
    result = ConclusionValidator().validate(
        report(),
        source_assessments=(source,),
        configuration_assessments=(),
        verifier_status="rejected",
    )

    assert result.result_state == "hypothesis"
    assert result.code_status == "hypothesis"
