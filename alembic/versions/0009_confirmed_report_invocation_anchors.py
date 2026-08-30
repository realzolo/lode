"""Make confirmed report anchors null-safe and invocation-exact.

Revision ID: 0009_report_invocation_anchors
Revises: 0008_confirmed_report_semantics
Create Date: 2026-08-30 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_report_invocation_anchors"
down_revision: str | None = "0008_confirmed_report_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _report_semantics_sql(
    *,
    null_safe_status: bool,
    require_synthesizer: bool,
    exact_finding_verifier: bool,
) -> str:
    synthesizer_declaration = (
        "synthesizer_row ai_invocations%ROWTYPE;" if require_synthesizer else ""
    )
    synthesizer_check = (
        """
                SELECT * INTO synthesizer_row
                FROM ai_invocations
                WHERE id = NEW.synthesizer_invocation_id;
                IF synthesizer_row.id IS NULL
                   OR synthesizer_row.investigation_id <> NEW.investigation_id
                   OR synthesizer_row.role <> 'synthesizer'
                   OR synthesizer_row.status <> 'succeeded' THEN
                    RAISE EXCEPTION
                        'confirmed report requires a successful investigation synthesizer'
                        USING ERRCODE = 'check_violation';
                END IF;
        """
        if require_synthesizer
        else ""
    )
    cause_expression = "NEW.incident_cause->>'status' = 'confirmed'"
    code_expression = "NEW.code_diagnosis->>'status' = 'confirmed'"
    if null_safe_status:
        cause_expression = f"COALESCE({cause_expression}, false)"
        code_expression = f"COALESCE({code_expression}, false)"
    finding_verifier_clause = (
        "AND finding.verifier_invocation_id = NEW.verifier_invocation_id"
        if exact_finding_verifier
        else ""
    )
    return f"""
            CREATE OR REPLACE FUNCTION enforce_report_semantics()
            RETURNS trigger AS $$
            DECLARE
                {synthesizer_declaration}
                verifier_row ai_invocations%ROWTYPE;
                cause_confirmed boolean;
                code_confirmed boolean;
            BEGIN
                IF NEW.result_state <> 'confirmed' THEN
                    RETURN NEW;
                END IF;

                {synthesizer_check}
                SELECT * INTO verifier_row
                FROM ai_invocations
                WHERE id = NEW.verifier_invocation_id;
                IF verifier_row.id IS NULL
                   OR verifier_row.investigation_id <> NEW.investigation_id
                   OR verifier_row.role <> 'verifier'
                   OR verifier_row.status <> 'succeeded' THEN
                    RAISE EXCEPTION
                        'confirmed report requires a successful investigation verifier'
                        USING ERRCODE = 'check_violation';
                END IF;

                cause_confirmed := {cause_expression};
                code_confirmed := {code_expression};
                IF NOT cause_confirmed AND NOT code_confirmed THEN
                    RAISE EXCEPTION
                        'confirmed report requires a confirmed incident cause or code diagnosis'
                        USING ERRCODE = 'check_violation';
                END IF;

                IF cause_confirmed THEN
                    IF jsonb_typeof(NEW.incident_cause->'evidence_refs')
                       IS DISTINCT FROM 'array' THEN
                        RAISE EXCEPTION
                            'confirmed incident cause requires an evidence reference array'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF jsonb_array_length(NEW.incident_cause->'evidence_refs') = 0 THEN
                        RAISE EXCEPTION
                            'confirmed incident cause requires owned evidence'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            NEW.incident_cause->'evidence_refs'
                        ) AS reference(value)
                        WHERE jsonb_typeof(reference.value) IS DISTINCT FROM 'number'
                           OR reference.value::text !~ '^[1-9][0-9]{{0,15}}$'
                    ) THEN
                        RAISE EXCEPTION
                            'confirmed incident cause contains an invalid evidence reference'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            NEW.incident_cause->'evidence_refs'
                        ) AS reference(value)
                        WHERE reference.value::text::bigint > 4503599627370495
                           OR NOT EXISTS (
                               SELECT 1
                               FROM evidence_artifacts AS artifact
                               WHERE artifact.id = reference.value::text::bigint
                                 AND artifact.investigation_id = NEW.investigation_id
                           )
                    ) THEN
                        RAISE EXCEPTION
                            'confirmed incident cause references evidence from another investigation'
                            USING ERRCODE = 'check_violation';
                    END IF;
                END IF;

                IF code_confirmed THEN
                    IF jsonb_typeof(NEW.code_diagnosis->'finding_refs')
                       IS DISTINCT FROM 'array' THEN
                        RAISE EXCEPTION
                            'confirmed code diagnosis requires a finding reference array'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF jsonb_array_length(NEW.code_diagnosis->'finding_refs') = 0 THEN
                        RAISE EXCEPTION
                            'confirmed code diagnosis requires a confirmed code finding'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            NEW.code_diagnosis->'finding_refs'
                        ) AS reference(value)
                        WHERE jsonb_typeof(reference.value) IS DISTINCT FROM 'number'
                           OR reference.value::text !~ '^[1-9][0-9]{{0,15}}$'
                    ) THEN
                        RAISE EXCEPTION
                            'confirmed code diagnosis contains an invalid finding reference'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            NEW.code_diagnosis->'finding_refs'
                        ) AS reference(value)
                        WHERE reference.value::text::bigint > 4503599627370495
                           OR NOT EXISTS (
                               SELECT 1
                               FROM investigation_code_findings AS finding
                               WHERE finding.id = reference.value::text::bigint
                                 AND finding.investigation_id = NEW.investigation_id
                                 AND finding.status = 'confirmed'
                                 {finding_verifier_clause}
                           )
                    ) THEN
                        RAISE EXCEPTION
                            'confirmed code diagnosis references an unconfirmed code finding'
                            USING ERRCODE = 'check_violation';
                    END IF;
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
    """


def upgrade() -> None:
    op.execute(
        sa.text(
            _report_semantics_sql(
                null_safe_status=True,
                require_synthesizer=True,
                exact_finding_verifier=True,
            )
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            _report_semantics_sql(
                null_safe_status=False,
                require_synthesizer=False,
                exact_finding_verifier=False,
            )
        )
    )
