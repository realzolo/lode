"""Align confirmed report persistence with incident and code conclusions.

Revision ID: 0008_confirmed_report_semantics
Revises: 0007_investigation_job_phase
Create Date: 2026-08-30 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_confirmed_report_semantics"
down_revision: str | None = "0007_investigation_job_phase"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION enforce_report_semantics()
            RETURNS trigger AS $$
            DECLARE
                verifier_row ai_invocations%ROWTYPE;
                cause_confirmed boolean;
                code_confirmed boolean;
            BEGIN
                IF NEW.result_state <> 'confirmed' THEN
                    RETURN NEW;
                END IF;

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

                cause_confirmed := NEW.incident_cause->>'status' = 'confirmed';
                code_confirmed := NEW.code_diagnosis->>'status' = 'confirmed';
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
                           OR reference.value::text !~ '^[1-9][0-9]{0,15}$'
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
                           OR reference.value::text !~ '^[1-9][0-9]{0,15}$'
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
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION enforce_report_semantics()
            RETURNS trigger AS $$
            DECLARE verifier_row ai_invocations%ROWTYPE;
            BEGIN
                IF NEW.result_state = 'confirmed' THEN
                    SELECT * INTO verifier_row
                    FROM ai_invocations
                    WHERE id = NEW.verifier_invocation_id;
                    IF verifier_row.id IS NULL
                       OR verifier_row.investigation_id <> NEW.investigation_id
                       OR verifier_row.role <> 'verifier'
                       OR verifier_row.status <> 'succeeded'
                       OR NOT EXISTS (
                           SELECT 1
                           FROM investigation_code_findings
                           WHERE investigation_id = NEW.investigation_id
                             AND status = 'confirmed'
                       ) THEN
                        RAISE EXCEPTION
                            'confirmed report requires verified confirmed code evidence';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
