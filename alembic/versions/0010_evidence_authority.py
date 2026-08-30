"""Replace repository roles and source authority semantics.

Revision ID: 0010_evidence_authority
Revises: 0009_report_invocation_anchors
Create Date: 2026-08-30 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_evidence_authority"
down_revision: str | None = "0009_report_invocation_anchors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This release intentionally has no historical execution-data compatibility path.
    op.execute(
        sa.text(
            "UPDATE workspaces SET ingestion_state = 'draft', "
            "ingestion_version = ingestion_version + 1, "
            "ingestion_start_position = NULL, ingestion_activation_kind = NULL, "
            "ingestion_started_at = NULL, ingestion_paused_at = NULL "
            "WHERE ingestion_state <> 'draft'"
        )
    )
    op.execute(
        sa.text(
            "TRUNCATE TABLE investigations, repository_analysis_jobs, "
            "workspace_repository_bindings, resource_graph_revisions, "
            "identity_resolutions, semantic_annotations, resource_observations, "
            "components CASCADE"
        )
    )

    op.drop_constraint(
        op.f("ck_workspace_repository_bindings_role"),
        "workspace_repository_bindings",
        type_="check",
    )
    op.add_column(
        "workspace_repository_bindings",
        sa.Column("analysis_mode", sa.Text(), nullable=False),
    )
    op.add_column(
        "workspace_repository_bindings",
        sa.Column("is_alert_source", sa.Boolean(), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_workspace_repository_bindings_analysis_mode"),
        "workspace_repository_bindings",
        "analysis_mode IN ('code', 'documentation')",
    )
    op.create_check_constraint(
        op.f("ck_workspace_repository_bindings_alert_source_requires_code"),
        "workspace_repository_bindings",
        "NOT is_alert_source OR analysis_mode = 'code'",
    )
    op.create_index(
        "uq_workspace_alert_source_active",
        "workspace_repository_bindings",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active' AND is_alert_source"),
    )
    op.drop_column("workspace_repository_bindings", "role")

    op.drop_constraint(
        op.f("ck_investigation_repository_snapshots_role"),
        "investigation_repository_snapshots",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_investigation_repository_snapshots_candidate_sha"),
        "investigation_repository_snapshots",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_investigation_repository_snapshots_frozen_revision_role"),
        "investigation_repository_snapshots",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_investigation_repository_snapshots_frozen_resolution_status"),
        "investigation_repository_snapshots",
        type_="check",
    )
    for column in (
        "analysis_mode",
        "is_alert_source",
        "frozen_revision_sha",
        "revision_policy",
        "revision_authority",
    ):
        column_type = sa.Boolean() if column == "is_alert_source" else sa.Text()
        op.add_column(
            "investigation_repository_snapshots",
            sa.Column(column, column_type, nullable=column == "frozen_revision_sha"),
        )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_analysis_mode"),
        "investigation_repository_snapshots",
        "analysis_mode IN ('code', 'documentation')",
    )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_alert_source_code"),
        "investigation_repository_snapshots",
        "NOT is_alert_source OR analysis_mode = 'code'",
    )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_revision_sha"),
        "investigation_repository_snapshots",
        "frozen_revision_sha IS NULL OR frozen_revision_sha ~ '^[0-9a-f]{40}$'",
    )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_revision_policy"),
        "investigation_repository_snapshots",
        "revision_policy IN ('alert_revision', 'bound_branch_head')",
    )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_revision_authority"),
        "investigation_repository_snapshots",
        "revision_authority IN ('authoritative', 'pending', 'unavailable')",
    )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_revision_policy_coherent"),
        "investigation_repository_snapshots",
        "(revision_policy = 'alert_revision' AND is_alert_source AND "
        "frozen_revision_sha IS NOT NULL AND revision_authority = 'authoritative') OR "
        "(revision_policy = 'bound_branch_head' AND ((frozen_revision_sha IS NULL AND "
        "revision_authority IN ('pending', 'unavailable')) OR "
        "(frozen_revision_sha IS NOT NULL AND revision_authority = 'authoritative')))",
    )
    for column in (
        "role",
        "frozen_candidate_sha",
        "frozen_revision_role",
        "frozen_resolution_status",
    ):
        op.drop_column("investigation_repository_snapshots", column)
    op.execute(
        sa.text(
            "DROP TRIGGER trg_investigation_repository_snapshots_immutable "
            "ON investigation_repository_snapshots"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_repository_snapshot_freeze() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'repository snapshot cannot be deleted';
                END IF;
                IF OLD.revision_policy <> 'bound_branch_head'
                   OR OLD.frozen_revision_sha IS NOT NULL
                   OR NEW.revision_policy <> OLD.revision_policy
                   OR NEW.is_alert_source <> OLD.is_alert_source
                   OR (to_jsonb(NEW) - ARRAY[
                        'frozen_revision_sha', 'revision_authority', 'snapshot_hash'
                      ]::text[])
                      <> (to_jsonb(OLD) - ARRAY[
                        'frozen_revision_sha', 'revision_authority', 'snapshot_hash'
                      ]::text[])
                   OR NOT (
                        (NEW.frozen_revision_sha IS NULL
                         AND NEW.revision_authority = 'unavailable')
                        OR
                        (NEW.frozen_revision_sha IS NOT NULL
                         AND NEW.revision_authority = 'authoritative')
                   ) THEN
                    RAISE EXCEPTION 'repository snapshot is frozen';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_investigation_repository_snapshots_freeze "
            "BEFORE UPDATE OR DELETE ON investigation_repository_snapshots "
            "FOR EACH ROW EXECUTE FUNCTION enforce_repository_snapshot_freeze()"
        )
    )

    op.drop_constraint(op.f("ck_source_revisions_revision_role"), "source_revisions", type_="check")
    op.drop_constraint(
        op.f("ck_source_revisions_resolution_status"), "source_revisions", type_="check"
    )
    op.drop_constraint("uq_source_revision", "source_revisions", type_="unique")
    op.add_column("source_revisions", sa.Column("revision_origin", sa.Text(), nullable=False))
    op.add_column("source_revisions", sa.Column("authority_status", sa.Text(), nullable=False))
    op.add_column("source_revisions", sa.Column("compatibility_status", sa.Text(), nullable=False))
    op.create_check_constraint(
        op.f("ck_source_revisions_revision_origin"),
        "source_revisions",
        "revision_origin IN ('alert_revision', 'bound_branch_head', 'runtime_observed')",
    )
    op.create_check_constraint(
        op.f("ck_source_revisions_authority_status"),
        "source_revisions",
        "authority_status IN ('authoritative', 'corroborated', 'contradicted', 'unavailable')",
    )
    op.create_check_constraint(
        op.f("ck_source_revisions_compatibility_status"),
        "source_revisions",
        "compatibility_status IN ('not_checked', 'compatible', 'incompatible')",
    )
    op.create_unique_constraint(
        "uq_source_revision",
        "source_revisions",
        ["investigation_id", "repository_snapshot_id", "revision_origin", "resolved_sha"],
    )
    op.drop_column("source_revisions", "revision_role")
    op.drop_column("source_revisions", "resolution_status")
    op.drop_column("source_revisions", "source_artifact_refs")

    op.drop_constraint(
        op.f("ck_source_assessments_runtime_match_status"),
        "source_assessments",
        type_="check",
    )
    op.add_column("source_assessments", sa.Column("authority_status", sa.Text(), nullable=False))
    op.add_column(
        "source_assessments", sa.Column("compatibility_status", sa.Text(), nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_source_assessments_authority_status"),
        "source_assessments",
        "authority_status IN ('authoritative', 'corroborated', 'contradicted', 'unavailable')",
    )
    op.create_check_constraint(
        op.f("ck_source_assessments_compatibility_status"),
        "source_assessments",
        "compatibility_status IN ('not_checked', 'compatible', 'incompatible')",
    )
    op.drop_column("source_assessments", "runtime_match_status")

    op.execute(sa.text("DROP TRIGGER trg_source_revisions_immutable ON source_revisions"))
    op.execute(sa.text("DROP TRIGGER trg_source_assessments_immutable ON source_assessments"))
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_source_revision_authority_transition() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   OR (to_jsonb(NEW) - ARRAY[
                        'authority_status', 'compatibility_status', 'resolution_basis'
                      ]::text[])
                      <> (to_jsonb(OLD) - ARRAY[
                        'authority_status', 'compatibility_status', 'resolution_basis'
                      ]::text[])
                   OR OLD.authority_status = 'contradicted'
                   OR OLD.compatibility_status = 'incompatible'
                   OR NEW.authority_status <> 'contradicted'
                   OR NEW.compatibility_status <> 'incompatible' THEN
                    RAISE EXCEPTION 'source revision authority is immutable except for contradiction';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_source_revisions_authority_transition "
            "BEFORE UPDATE OR DELETE ON source_revisions "
            "FOR EACH ROW EXECUTE FUNCTION enforce_source_revision_authority_transition()"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_source_assessment_authority_transition() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   OR (to_jsonb(NEW) - ARRAY[
                        'authority_status', 'compatibility_status', 'mismatch_reasons',
                        'evidence_refs', 'assessment_hash'
                      ]::text[])
                      <> (to_jsonb(OLD) - ARRAY[
                        'authority_status', 'compatibility_status', 'mismatch_reasons',
                        'evidence_refs', 'assessment_hash'
                      ]::text[])
                   OR OLD.authority_status = 'contradicted'
                   OR OLD.compatibility_status = 'incompatible'
                   OR NEW.authority_status <> 'contradicted'
                   OR NEW.compatibility_status <> 'incompatible' THEN
                    RAISE EXCEPTION 'source assessment is immutable except for contradiction';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_source_assessments_authority_transition "
            "BEFORE UPDATE OR DELETE ON source_assessments "
            "FOR EACH ROW EXECUTE FUNCTION enforce_source_assessment_authority_transition()"
        )
    )

    op.drop_constraint(
        op.f("ck_investigation_code_findings_revision_role"),
        "investigation_code_findings",
        type_="check",
    )
    op.add_column(
        "investigation_code_findings",
        sa.Column("revision_origin", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_investigation_code_findings_revision_origin"),
        "investigation_code_findings",
        "revision_origin IS NULL OR revision_origin IN "
        "('alert_revision', 'bound_branch_head', 'runtime_observed')",
    )
    op.drop_column("investigation_code_findings", "revision_role")
    op.drop_constraint(
        "uq_investigation_operation_fingerprint",
        "investigation_operations",
        type_="unique",
    )
    op.drop_constraint(
        "uq_native_read_candidate_hash",
        "native_read_candidates",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_investigation_reports_schema_version"),
        "investigation_reports",
        type_="check",
    )
    op.alter_column(
        "investigation_reports",
        "schema_version",
        server_default="investigation-report.v2",
    )
    op.create_check_constraint(
        op.f("ck_investigation_reports_schema_version"),
        "investigation_reports",
        "schema_version = 'investigation-report.v2'",
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION enforce_code_finding_anchor() RETURNS trigger AS $$
            DECLARE artifact_row evidence_artifacts%ROWTYPE;
            DECLARE assessment_row source_assessments%ROWTYPE;
            DECLARE revision_row source_revisions%ROWTYPE;
            DECLARE verifier_row ai_invocations%ROWTYPE;
            BEGIN
                IF NEW.status IN ('confirmed', 'hypothesis') THEN
                    SELECT * INTO artifact_row FROM evidence_artifacts WHERE id = NEW.source_artifact_id;
                    SELECT * INTO assessment_row FROM source_assessments WHERE id = NEW.source_assessment_id;
                    SELECT * INTO revision_row FROM source_revisions WHERE id = assessment_row.source_revision_id;
                    IF artifact_row.id IS NULL OR assessment_row.id IS NULL OR revision_row.id IS NULL
                       OR artifact_row.investigation_id <> NEW.investigation_id
                       OR assessment_row.investigation_id <> NEW.investigation_id
                       OR revision_row.investigation_id <> NEW.investigation_id
                       OR artifact_row.artifact_kind <> 'source_file'
                       OR revision_row.resolved_sha <> NEW.revision
                       OR revision_row.revision_origin <> NEW.revision_origin
                       OR (artifact_row.provenance->>'repository_id')::bigint <> NEW.repository_id
                       OR artifact_row.provenance->>'revision' <> NEW.revision
                       OR artifact_row.provenance->>'path' <> NEW.path
                       OR artifact_row.provenance->>'symbol' <> NEW.symbol
                       OR (artifact_row.provenance->>'start_line')::integer <> NEW.start_line
                       OR (artifact_row.provenance->>'end_line')::integer <> NEW.end_line THEN
                        RAISE EXCEPTION 'code finding does not exactly match its source artifact';
                    END IF;
                END IF;
                IF NEW.status = 'confirmed' THEN
                    IF assessment_row.authority_status NOT IN ('authoritative', 'corroborated')
                       OR assessment_row.compatibility_status = 'incompatible' THEN
                        RAISE EXCEPTION 'confirmed finding requires authoritative compatible source';
                    END IF;
                    SELECT * INTO verifier_row FROM ai_invocations WHERE id = NEW.verifier_invocation_id;
                    IF verifier_row.id IS NULL OR verifier_row.investigation_id <> NEW.investigation_id
                       OR verifier_row.role <> 'verifier' OR verifier_row.status <> 'succeeded' THEN
                        RAISE EXCEPTION 'confirmed finding requires a successful independent verifier';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError("0010_evidence_authority is an intentionally destructive replacement")
