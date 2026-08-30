"""Canonicalize immutable evidence access budget revisions.

Revision ID: 0005_canonical_evidence_budget
Revises: 0004_workspace_ingestion_state
Create Date: 2026-08-29 15:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_canonical_evidence_budget"
down_revision: str | None = "0004_workspace_ingestion_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CANONICAL_POLICY = """
jsonb_build_object(
    'max_result_limit', COALESCE(
        execution_budget_policy->'max_result_limit',
        execution_budget_policy->'max_rows',
        to_jsonb(1000)
    ),
    'max_timeout_ms', COALESCE(
        execution_budget_policy->'max_timeout_ms',
        execution_budget_policy->'timeout_ms',
        to_jsonb(5000)
    ),
    'max_output_bytes', COALESCE(
        execution_budget_policy->'max_output_bytes',
        to_jsonb(1000000)
    ),
    'max_total_output_bytes', COALESCE(
        execution_budget_policy->'max_total_output_bytes',
        to_jsonb(20000000)
    ),
    'max_native_reads', COALESCE(
        execution_budget_policy->'max_native_reads',
        to_jsonb(8)
    ),
    'max_window_seconds', COALESCE(
        execution_budget_policy->'max_window_seconds',
        to_jsonb(7200)
    ),
    'max_parallel_operations', COALESCE(
        execution_budget_policy->'max_parallel_operations',
        to_jsonb(1)
    ),
    'estimated_cost', COALESCE(
        execution_budget_policy->'estimated_cost',
        to_jsonb(0.0)
    )
)
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            WITH latest AS (
                SELECT DISTINCT ON (connector_id) *
                FROM evidence_access_scopes
                ORDER BY connector_id, revision DESC
            ), normalized AS (
                SELECT latest.*, {_CANONICAL_POLICY} AS canonical_policy
                FROM latest
            )
            INSERT INTO evidence_access_scopes (
                connector_id, allowed_languages, scope_config, schema_catalog,
                schema_catalog_revision, read_policy_revision,
                execution_budget_policy, normalization_policy_revision, revision
            )
            SELECT
                connector_id, allowed_languages, scope_config, schema_catalog,
                schema_catalog_revision, read_policy_revision + 1,
                canonical_policy, normalization_policy_revision, revision + 1
            FROM normalized
            WHERE execution_budget_policy IS DISTINCT FROM canonical_policy
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION evidence_budget_policy_is_canonical(candidate jsonb)
            RETURNS boolean AS $$
            DECLARE
                actual_keys text[];
                integer_key text;
            BEGIN
                IF jsonb_typeof(candidate) IS DISTINCT FROM 'object' THEN
                    RETURN false;
                END IF;
                SELECT array_agg(key ORDER BY key)
                INTO actual_keys
                FROM jsonb_object_keys(candidate) AS keys(key);
                IF actual_keys IS DISTINCT FROM ARRAY[
                    'estimated_cost',
                    'max_native_reads',
                    'max_output_bytes',
                    'max_parallel_operations',
                    'max_result_limit',
                    'max_timeout_ms',
                    'max_total_output_bytes',
                    'max_window_seconds'
                ]::text[] THEN
                    RETURN false;
                END IF;
                FOREACH integer_key IN ARRAY ARRAY[
                    'max_native_reads',
                    'max_output_bytes',
                    'max_parallel_operations',
                    'max_result_limit',
                    'max_timeout_ms',
                    'max_total_output_bytes',
                    'max_window_seconds'
                ]::text[] LOOP
                    IF jsonb_typeof(candidate->integer_key) IS DISTINCT FROM 'number'
                       OR candidate->>integer_key !~ '^[1-9][0-9]*$' THEN
                        RETURN false;
                    END IF;
                END LOOP;
                IF jsonb_typeof(candidate->'estimated_cost') IS DISTINCT FROM 'number'
                   OR (candidate->>'estimated_cost')::numeric < 0
                   OR (candidate->>'max_output_bytes')::bigint
                      > (candidate->>'max_total_output_bytes')::bigint THEN
                    RETURN false;
                END IF;
                RETURN true;
            END;
            $$ LANGUAGE plpgsql IMMUTABLE
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_canonical_evidence_budget_policy()
            RETURNS trigger AS $$
            BEGIN
                IF NOT evidence_budget_policy_is_canonical(NEW.execution_budget_policy) THEN
                    RAISE EXCEPTION 'execution budget policy is not canonical'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_evidence_access_scopes_budget_canonical
            BEFORE INSERT OR UPDATE ON evidence_access_scopes
            FOR EACH ROW EXECUTE FUNCTION enforce_canonical_evidence_budget_policy()
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM (
                        SELECT DISTINCT ON (connector_id) execution_budget_policy
                        FROM evidence_access_scopes
                        ORDER BY connector_id, revision DESC
                    ) AS latest
                    WHERE NOT evidence_budget_policy_is_canonical(execution_budget_policy)
                ) THEN
                    RAISE EXCEPTION 'latest evidence access budget migration is incomplete';
                END IF;
            END;
            $$
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_evidence_access_scopes_budget_canonical "
            "ON evidence_access_scopes"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_canonical_evidence_budget_policy()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS evidence_budget_policy_is_canonical(jsonb)"))
    op.execute(sa.text("ALTER TABLE evidence_access_scopes DISABLE TRIGGER trg_evidence_access_scopes_immutable"))
    op.execute(
        sa.text(
            f"""
            WITH predecessors AS (
                SELECT
                    older.connector_id,
                    older.revision AS older_revision,
                    older.read_policy_revision AS older_policy_revision,
                    {_CANONICAL_POLICY} AS canonical_policy
                FROM evidence_access_scopes AS older
            )
            DELETE FROM evidence_access_scopes AS newer
            USING predecessors
            WHERE newer.connector_id = predecessors.connector_id
              AND newer.revision = predecessors.older_revision + 1
              AND newer.read_policy_revision = predecessors.older_policy_revision + 1
              AND newer.execution_budget_policy = predecessors.canonical_policy
            """
        )
    )
    op.execute(sa.text("ALTER TABLE evidence_access_scopes ENABLE TRIGGER trg_evidence_access_scopes_immutable"))
