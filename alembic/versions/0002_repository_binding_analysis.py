"""Repository binding branch policy and durable analysis diagnostics.

Revision ID: 0002_repository_binding_analysis
Revises: 0001_initial
Create Date: 2026-08-28 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_repository_binding_analysis"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ZERO_HASH = "0" * 64
_BINDING_TABLE = "workspace_repository_bindings"
_ANALYSIS_TABLE = "repository_analysis_jobs"
_SNAPSHOT_TABLE = "investigation_repository_snapshots"


def upgrade() -> None:
    op.add_column(
        _BINDING_TABLE,
        sa.Column("branch_mode", sa.Text(), server_default="default", nullable=False),
    )
    op.add_column(_BINDING_TABLE, sa.Column("branch_name", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_workspace_repository_bindings_branch_selection"),
        _BINDING_TABLE,
        "(branch_mode = 'default' AND branch_name IS NULL) OR "
        "(branch_mode = 'branch' AND branch_name IS NOT NULL AND char_length(branch_name) > 0)",
    )

    op.add_column(
        _ANALYSIS_TABLE,
        sa.Column(
            "binding_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    # Legacy queued/running jobs predate immutable analysis inputs. Freeze the
    # bindings visible during this upgrade so a complete legacy task can still
    # run against one stable configuration.
    op.execute(
        sa.text(
            "UPDATE repository_analysis_jobs AS job "
            "SET binding_snapshot = COALESCE(("
            "SELECT jsonb_agg(jsonb_build_object("
            "'binding_id', binding.id, "
            "'configuration_revision', binding.descriptor_revision, "
            "'repository_id', repository.id, "
            "'account_connection_id', binding.account_connection_id, "
            "'role', binding.role, "
            "'branch_mode', binding.branch_mode, "
            "'effective_branch', CASE "
            "WHEN binding.branch_mode = 'branch' THEN binding.branch_name "
            "ELSE repository.default_branch END"
            ") ORDER BY binding.id) "
            "FROM workspace_repository_bindings AS binding "
            "JOIN git_repositories AS repository ON repository.id = binding.repository_id "
            "WHERE binding.id = ANY(job.requested_binding_ids)"
            "), '[]'::jsonb) "
            "WHERE job.state IN ('queued', 'running')"
        )
    )
    op.add_column(
        _ANALYSIS_TABLE,
        sa.Column("input_hash", sa.Text(), server_default=_ZERO_HASH, nullable=False),
    )
    op.add_column(
        _ANALYSIS_TABLE,
        sa.Column("result_status", sa.Text(), server_default="pending", nullable=False),
    )
    op.add_column(
        _ANALYSIS_TABLE,
        sa.Column(
            "source_branches",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE repository_analysis_jobs "
            "SET result_status = CASE "
            "WHEN state = 'succeeded' AND issue_count > 0 THEN 'warnings' "
            "WHEN state = 'succeeded' THEN 'clean' "
            "WHEN state = 'failed' THEN 'failed' "
            "ELSE 'pending' END"
        )
    )
    op.execute(
        sa.text(
            "UPDATE repository_analysis_jobs "
            "SET state = 'failed', result_status = 'failed', "
            "failure_code = 'repository_analysis_failed', lease_owner = NULL, "
            "lease_expires_at = NULL, finished_at = COALESCE(finished_at, now()) "
            "WHERE state IN ('queued', 'running') "
            "AND jsonb_array_length(binding_snapshot) <> cardinality(requested_binding_ids)"
        )
    )
    op.execute(
        sa.text(
            "UPDATE repository_analysis_jobs "
            "SET failure_code = 'repository_analysis_failed' "
            "WHERE failure_code = 'repository_manifest_invalid'"
        )
    )
    op.drop_constraint(op.f("ck_repository_analysis_jobs_failure_code"), _ANALYSIS_TABLE)
    op.drop_constraint(op.f("ck_repository_analysis_jobs_state_shape"), _ANALYSIS_TABLE)
    op.create_check_constraint(
        op.f("ck_repository_analysis_jobs_binding_snapshot_array"),
        _ANALYSIS_TABLE,
        "jsonb_typeof(binding_snapshot) = 'array'",
    )
    op.create_check_constraint(
        op.f("ck_repository_analysis_jobs_input_hash_sha256"),
        _ANALYSIS_TABLE,
        "input_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_repository_analysis_jobs_result_status"),
        _ANALYSIS_TABLE,
        "result_status IN ('pending', 'clean', 'warnings', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_repository_analysis_jobs_failure_code"),
        _ANALYSIS_TABLE,
        "failure_code IS NULL OR failure_code IN ("
        "'repository_access_unavailable', 'repository_branch_unavailable', "
        "'repository_checkout_failed', 'repository_scan_limit_exceeded', "
        "'repository_analysis_failed')",
    )
    op.create_check_constraint(
        op.f("ck_repository_analysis_jobs_state_shape"),
        _ANALYSIS_TABLE,
        "(state = 'queued' AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND finished_at IS NULL AND failure_code IS NULL AND result_status = 'pending') OR "
        "(state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND started_at IS NOT NULL AND finished_at IS NULL AND failure_code IS NULL "
        "AND result_status = 'pending') OR "
        "(state = 'succeeded' AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND graph_revision_id IS NOT NULL AND finished_at IS NOT NULL "
        "AND failure_code IS NULL AND result_status IN ('clean', 'warnings')) OR "
        "(state = 'failed' AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND finished_at IS NOT NULL AND failure_code IS NOT NULL AND result_status = 'failed')",
    )

    op.create_table(
        "repository_analysis_issues",
        sa.Column("id", sa.BigInteger(), server_default=sa.text("next_lode_id()"), nullable=False),
        sa.Column("repository_analysis_job_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_binding_id", sa.BigInteger(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Text(), server_default="warning", nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal >= 0", name=op.f("ck_repository_analysis_issues_ordinal_nonnegative")
        ),
        sa.CheckConstraint(
            "severity IN ('warning', 'error')", name=op.f("ck_repository_analysis_issues_severity")
        ),
        sa.CheckConstraint(
            "char_length(code) BETWEEN 1 AND 100",
            name=op.f("ck_repository_analysis_issues_code_length"),
        ),
        sa.CheckConstraint(
            "char_length(detail) <= 500",
            name=op.f("ck_repository_analysis_issues_detail_length"),
        ),
        sa.ForeignKeyConstraint(
            ["repository_analysis_job_id"],
            ["repository_analysis_jobs.id"],
            name=op.f(
                "fk_repository_analysis_issues_repository_analysis_job_id_repository_analysis_jobs"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_binding_id"],
            ["workspace_repository_bindings.id"],
            name=op.f(
                "fk_repository_analysis_issues_repository_binding_id_workspace_repository_bindings"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repository_analysis_issues")),
        sa.UniqueConstraint("repository_analysis_job_id", "ordinal", name="uq_repository_analysis_issue"),
    )
    op.create_index(
        "ix_repository_analysis_issues_job",
        "repository_analysis_issues",
        ["repository_analysis_job_id", "ordinal"],
        unique=False,
    )

    op.add_column(
        _SNAPSHOT_TABLE,
        sa.Column("branch_mode", sa.Text(), server_default="default", nullable=False),
    )
    op.add_column(
        _SNAPSHOT_TABLE,
        sa.Column("selected_branch", sa.Text(), server_default="main", nullable=False),
    )
    op.execute(
        sa.text(
            "UPDATE investigation_repository_snapshots "
            "SET selected_branch = default_branch"
        )
    )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_branch_mode"),
        _SNAPSHOT_TABLE,
        "branch_mode IN ('default', 'branch')",
    )
    op.create_check_constraint(
        op.f("ck_investigation_repository_snapshots_selected_branch_nonempty"),
        _SNAPSHOT_TABLE,
        "char_length(selected_branch) > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_investigation_repository_snapshots_selected_branch_nonempty"),
        _SNAPSHOT_TABLE,
    )
    op.drop_constraint(
        op.f("ck_investigation_repository_snapshots_branch_mode"),
        _SNAPSHOT_TABLE,
    )
    op.drop_column(_SNAPSHOT_TABLE, "selected_branch")
    op.drop_column(_SNAPSHOT_TABLE, "branch_mode")

    op.drop_index("ix_repository_analysis_issues_job", table_name="repository_analysis_issues")
    op.drop_table("repository_analysis_issues")

    op.execute(
        sa.text(
            "UPDATE repository_analysis_jobs "
            "SET failure_code = 'repository_analysis_failed' "
            "WHERE failure_code IN ('repository_branch_unavailable', 'repository_scan_limit_exceeded')"
        )
    )
    op.drop_constraint(op.f("ck_repository_analysis_jobs_state_shape"), _ANALYSIS_TABLE)
    op.drop_constraint(op.f("ck_repository_analysis_jobs_failure_code"), _ANALYSIS_TABLE)
    op.drop_constraint(op.f("ck_repository_analysis_jobs_result_status"), _ANALYSIS_TABLE)
    op.drop_constraint(op.f("ck_repository_analysis_jobs_input_hash_sha256"), _ANALYSIS_TABLE)
    op.drop_constraint(op.f("ck_repository_analysis_jobs_binding_snapshot_array"), _ANALYSIS_TABLE)
    op.create_check_constraint(
        op.f("ck_repository_analysis_jobs_failure_code"),
        _ANALYSIS_TABLE,
        "failure_code IS NULL OR failure_code IN ("
        "'repository_access_unavailable', 'repository_checkout_failed', "
        "'repository_manifest_invalid', 'repository_analysis_failed')",
    )
    op.create_check_constraint(
        op.f("ck_repository_analysis_jobs_state_shape"),
        _ANALYSIS_TABLE,
        "(state = 'queued' AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND finished_at IS NULL AND failure_code IS NULL) OR "
        "(state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND started_at IS NOT NULL AND finished_at IS NULL AND failure_code IS NULL) OR "
        "(state = 'succeeded' AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND graph_revision_id IS NOT NULL AND finished_at IS NOT NULL "
        "AND failure_code IS NULL) OR "
        "(state = 'failed' AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND finished_at IS NOT NULL AND failure_code IS NOT NULL)",
    )
    op.drop_column(_ANALYSIS_TABLE, "source_branches")
    op.drop_column(_ANALYSIS_TABLE, "result_status")
    op.drop_column(_ANALYSIS_TABLE, "input_hash")
    op.drop_column(_ANALYSIS_TABLE, "binding_snapshot")

    op.drop_constraint(op.f("ck_workspace_repository_bindings_branch_selection"), _BINDING_TABLE)
    op.drop_column(_BINDING_TABLE, "branch_name")
    op.drop_column(_BINDING_TABLE, "branch_mode")
