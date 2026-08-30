"""Bind ClickHouse evidence scopes to the ClickHouse SQL dialect.

Revision ID: 0011_clickhouse_sql_scope
Revises: 0010_evidence_authority
Create Date: 2026-08-30 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_clickhouse_sql_scope"
down_revision: str | None = "0010_evidence_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dialect_function(*, include_clickhouse: bool) -> str:
    clickhouse_case = "\n                    WHEN 'clickhouse' THEN 'clickhouse'" if include_clickhouse else ""
    return f"""
        CREATE OR REPLACE FUNCTION enforce_sql_scope_dialect()
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
                WHEN 'mysql' THEN 'mysql'{clickhouse_case}
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


def upgrade() -> None:
    op.execute(sa.text(_dialect_function(include_clickhouse=True)))


def downgrade() -> None:
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
                    WHERE connector.kind = 'clickhouse'
                      AND 'sql' = ANY(scope.allowed_languages)
                ) THEN
                    RAISE EXCEPTION 'cannot downgrade while ClickHouse SQL scopes exist';
                END IF;
            END;
            $$
            """
        )
    )
    op.execute(sa.text(_dialect_function(include_clickhouse=False)))
