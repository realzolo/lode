"""Keep credential rejection out of server-generated Schema metadata.

Revision ID: 0003_schema_catalog_secret_scope
Revises: 0002_repository_binding_analysis
Create Date: 2026-08-28 18:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_schema_catalog_secret_scope"
down_revision: str | None = "0002_repository_binding_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION_TEMPLATE = """
CREATE OR REPLACE FUNCTION reject_secret_json() RETURNS trigger AS $$
DECLARE candidate jsonb;
DECLARE new_row jsonb;
BEGIN
    new_row = to_jsonb(NEW);
    candidate = COALESCE(new_row->'config', '{{}}'::jsonb)
        || COALESCE(new_row->'scope_config', '{{}}'::jsonb){catalog_candidate};
    IF EXISTS (
        SELECT 1
        FROM jsonb_path_query(
            candidate,
            '$.** ? (@.type() == "object")'
        ) AS object_nodes(value)
        CROSS JOIN LATERAL jsonb_object_keys(object_nodes.value) AS keys(key)
        WHERE keys.key ~* '^(password|passwd|secret|token|api_key|access_key|authorization|cookie)$'
    ) THEN
        RAISE EXCEPTION 'ordinary JSON config may not contain credentials';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    # Catalogs contain provider-owned identifiers, not row values. A legitimate
    # table or column may therefore have a credential-like name.
    op.execute(sa.text(_FUNCTION_TEMPLATE.format(catalog_candidate="")))


def downgrade() -> None:
    op.execute(
        sa.text(
            _FUNCTION_TEMPLATE.format(
                catalog_candidate="\n        || COALESCE(new_row->'schema_catalog', '{}'::jsonb)"
            )
        )
    )
