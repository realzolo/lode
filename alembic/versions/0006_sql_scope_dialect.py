"""Require explicit SQL dialect ownership in access scopes.

Revision ID: 0006_sql_scope_dialect
Revises: 0005_canonical_evidence_budget
Create Date: 2026-08-29 15:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_sql_scope_dialect"
down_revision: str | None = "0005_canonical_evidence_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            WITH latest AS (
                SELECT DISTINCT ON (scope.connector_id)
                    scope.*,
                    CASE connector.kind
                        WHEN 'postgresql' THEN 'postgres'
                        WHEN 'mysql' THEN 'mysql'
                    END AS expected_dialect
                FROM evidence_access_scopes AS scope
                JOIN evidence_connectors AS connector ON connector.id = scope.connector_id
                WHERE connector.kind IN ('postgresql', 'mysql')
                ORDER BY scope.connector_id, scope.revision DESC
            )
            INSERT INTO evidence_access_scopes (
                connector_id, allowed_languages, scope_config, schema_catalog,
                schema_catalog_revision, read_policy_revision,
                execution_budget_policy, normalization_policy_revision, revision
            )
            SELECT
                connector_id, allowed_languages,
                scope_config || jsonb_build_object('dialect', expected_dialect),
                schema_catalog, schema_catalog_revision, read_policy_revision + 1,
                execution_budget_policy, normalization_policy_revision, revision + 1
            FROM latest
            WHERE scope_config->>'dialect' IS DISTINCT FROM expected_dialect
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_sql_scope_dialect()
            RETURNS trigger AS $$
            DECLARE
                connector_kind text;
                expected_dialect text;
            BEGIN
                SELECT kind INTO STRICT connector_kind
                FROM evidence_connectors
                WHERE id = NEW.connector_id;
                expected_dialect := CASE connector_kind
                    WHEN 'postgresql' THEN 'postgres'
                    WHEN 'mysql' THEN 'mysql'
                    ELSE NULL
                END;
                IF expected_dialect IS NOT NULL AND (
                    NOT ('sql' = ANY(NEW.allowed_languages))
                    OR NEW.scope_config->>'dialect' IS DISTINCT FROM expected_dialect
                ) THEN
                    RAISE EXCEPTION 'SQL connector scope must own its matching SQL dialect'
                        USING ERRCODE = 'check_violation';
                ELSIF expected_dialect IS NULL AND 'sql' = ANY(NEW.allowed_languages) THEN
                    RAISE EXCEPTION 'non-SQL connector scope cannot own SQL language'
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
            CREATE TRIGGER trg_evidence_access_scopes_sql_dialect
            BEFORE INSERT OR UPDATE ON evidence_access_scopes
            FOR EACH ROW EXECUTE FUNCTION enforce_sql_scope_dialect()
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
                    FROM evidence_connectors AS connector
                    JOIN LATERAL (
                        SELECT allowed_languages, scope_config
                        FROM evidence_access_scopes
                        WHERE connector_id = connector.id
                        ORDER BY revision DESC
                        LIMIT 1
                    ) AS scope ON true
                    WHERE connector.kind IN ('postgresql', 'mysql')
                      AND (
                          NOT ('sql' = ANY(scope.allowed_languages))
                          OR scope.scope_config->>'dialect' IS DISTINCT FROM
                             CASE connector.kind
                                 WHEN 'postgresql' THEN 'postgres'
                                 WHEN 'mysql' THEN 'mysql'
                             END
                      )
                ) THEN
                    RAISE EXCEPTION 'latest SQL access scope dialect migration is incomplete';
                END IF;
            END;
            $$
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_evidence_access_scopes_sql_dialect "
            "ON evidence_access_scopes"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_sql_scope_dialect()"))
    op.execute(
        sa.text(
            "ALTER TABLE evidence_access_scopes "
            "DISABLE TRIGGER trg_evidence_access_scopes_immutable"
        )
    )
    op.execute(
        sa.text(
            """
            WITH predecessors AS (
                SELECT
                    older.connector_id,
                    older.revision AS older_revision,
                    older.read_policy_revision AS older_policy_revision,
                    older.scope_config || jsonb_build_object(
                        'dialect',
                        CASE connector.kind
                            WHEN 'postgresql' THEN 'postgres'
                            WHEN 'mysql' THEN 'mysql'
                        END
                    ) AS migrated_scope
                FROM evidence_access_scopes AS older
                JOIN evidence_connectors AS connector ON connector.id = older.connector_id
                WHERE connector.kind IN ('postgresql', 'mysql')
                  AND older.scope_config->>'dialect' IS DISTINCT FROM
                      CASE connector.kind
                          WHEN 'postgresql' THEN 'postgres'
                          WHEN 'mysql' THEN 'mysql'
                      END
            )
            DELETE FROM evidence_access_scopes AS newer
            USING predecessors
            WHERE newer.connector_id = predecessors.connector_id
              AND newer.revision = predecessors.older_revision + 1
              AND newer.read_policy_revision = predecessors.older_policy_revision + 1
              AND newer.scope_config = predecessors.migrated_scope
            """
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE evidence_access_scopes "
            "ENABLE TRIGGER trg_evidence_access_scopes_immutable"
        )
    )
