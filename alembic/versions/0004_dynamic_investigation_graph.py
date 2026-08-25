"""Replace fixed investigation stages with a capability-driven plan graph."""

from alembic import op
from sqlalchemy import text


revision = "0004_dynamic_graph"
down_revision = "0003_execution_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = [
        "ALTER TABLE investigations DROP CONSTRAINT IF EXISTS status;",
        "ALTER TABLE investigations DROP CONSTRAINT IF EXISTS investigations_status_check;",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS parent_investigation_id bigint REFERENCES investigations(id) ON DELETE SET NULL;",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS result_state text NOT NULL DEFAULT 'unavailable';",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS review_required boolean NOT NULL DEFAULT false;",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS review_reasons jsonb NOT NULL DEFAULT '[]'::jsonb;",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS audit_status text NOT NULL DEFAULT 'auditable';",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS engine_version text;",
        "UPDATE investigations SET status = 'completed', result_state = CASE WHEN confidence IS NULL OR confidence < 0.25 THEN 'insufficient' ELSE 'provisional' END WHERE status = 'needs_review';",
        "ALTER TABLE investigations ADD CONSTRAINT status CHECK (status IN ('queued', 'running', 'completed', 'failed'));",
        "ALTER TABLE investigations ADD CONSTRAINT result_state CHECK (result_state IN ('confirmed', 'provisional', 'insufficient', 'unavailable'));",
        "ALTER TABLE investigations ADD CONSTRAINT audit_status CHECK (audit_status IN ('auditable', 'unverifiable', 'violated'));",
        "ALTER TABLE source_revisions DROP CONSTRAINT IF EXISTS status;",
        "ALTER TABLE source_revisions DROP CONSTRAINT IF EXISTS source_revisions_status_check;",
        "ALTER TABLE source_revisions ADD COLUMN IF NOT EXISTS resolution_basis text;",
        "ALTER TABLE source_revisions ADD CONSTRAINT status CHECK (status IN ('queued', 'resolved', 'unresolved', 'failed', 'not_configured'));",
        "ALTER TABLE evidence_artifacts DROP CONSTRAINT IF EXISTS type;",
        "ALTER TABLE evidence_artifacts DROP CONSTRAINT IF EXISTS evidence_artifacts_artifact_type_check;",
        "ALTER TABLE evidence_artifacts ADD CONSTRAINT type CHECK (artifact_type IN ('alert', 'source_file', 'source_diff', 'log', 'metric', 'trace', 'dependency', 'database', 'operator_input'));",
        """
        CREATE TABLE investigation_plan_revisions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            revision integer NOT NULL,
            trigger_node_id bigint,
            decision text NOT NULL CHECK (decision IN ('initial', 'continue', 'conclude', 'request_evidence')),
            rationale text NOT NULL,
            capability_catalog jsonb NOT NULL DEFAULT '{}'::jsonb,
            evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (investigation_id, revision)
        );
        """,
        """
        CREATE TABLE investigation_plan_nodes (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id text NOT NULL UNIQUE,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            plan_revision_id bigint NOT NULL REFERENCES investigation_plan_revisions(id) ON DELETE CASCADE,
            stage_id bigint REFERENCES investigation_stages(id) ON DELETE SET NULL,
            capability text NOT NULL,
            title text NOT NULL,
            objective text NOT NULL,
            selection_reason text NOT NULL,
            expected_evidence text NOT NULL,
            decision_rule text NOT NULL,
            budget jsonb NOT NULL DEFAULT '{}'::jsonb,
            stop_condition text NOT NULL DEFAULT '',
            tool_input jsonb NOT NULL DEFAULT '{}'::jsonb,
            ai_participated boolean NOT NULL DEFAULT false,
            status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'canceled')),
            input_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            output_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            outcome jsonb NOT NULL DEFAULT '{}'::jsonb,
            failure_code text,
            failure_detail text,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX ix_investigation_plan_nodes_run_status ON investigation_plan_nodes(investigation_id, status, created_at);",
        "ALTER TABLE investigation_plan_revisions ADD CONSTRAINT investigation_plan_revisions_trigger_node_id_fkey FOREIGN KEY (trigger_node_id) REFERENCES investigation_plan_nodes(id) ON DELETE SET NULL;",
        """
        CREATE TABLE investigation_plan_node_dependencies (
            node_id bigint NOT NULL REFERENCES investigation_plan_nodes(id) ON DELETE CASCADE,
            depends_on_node_id bigint NOT NULL REFERENCES investigation_plan_nodes(id) ON DELETE CASCADE,
            PRIMARY KEY (node_id, depends_on_node_id),
            CHECK (node_id <> depends_on_node_id)
        );
        """,
        "ALTER TABLE investigation_execution_events DROP CONSTRAINT IF EXISTS phase;",
        "ALTER TABLE investigation_execution_events DROP CONSTRAINT IF EXISTS investigation_execution_events_phase_check;",
        "ALTER TABLE investigation_execution_events ADD CONSTRAINT phase CHECK (phase IN ('started', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured', 'canceled'));",
        "ALTER TABLE investigation_execution_events ALTER COLUMN stage_id DROP NOT NULL;",
        "ALTER TABLE investigation_execution_events ADD COLUMN IF NOT EXISTS node_id bigint REFERENCES investigation_plan_nodes(id) ON DELETE CASCADE;",
        "DROP INDEX IF EXISTS ix_investigation_execution_events_operation;",
        "CREATE INDEX ix_investigation_execution_events_operation ON investigation_execution_events(investigation_id, stage_id, node_id, operation_id, sequence);",
        """
        CREATE TABLE investigation_ai_invocations (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            node_id bigint REFERENCES investigation_plan_nodes(id) ON DELETE SET NULL,
            purpose text NOT NULL,
            provider text,
            model text,
            status text NOT NULL CHECK (status IN ('succeeded', 'failed', 'fallback')),
            latency_ms integer NOT NULL DEFAULT 0,
            input_tokens integer,
            output_tokens integer,
            total_tokens integer,
            token_source text NOT NULL DEFAULT 'unavailable' CHECK (token_source IN ('provider', 'estimated', 'unavailable')),
            error_code text,
            summary text NOT NULL DEFAULT '',
            evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX ix_investigation_ai_invocations_run ON investigation_ai_invocations(investigation_id, node_id, created_at);",
        """
        CREATE TABLE investigation_findings (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            node_id bigint REFERENCES investigation_plan_nodes(id) ON DELETE SET NULL,
            ordinal integer NOT NULL,
            kind text NOT NULL CHECK (kind IN ('fact', 'hypothesis', 'counter_evidence', 'evidence_gap', 'conclusion')),
            status text NOT NULL CHECK (status IN ('supported', 'open', 'refuted', 'required')),
            text text NOT NULL,
            rationale text NOT NULL DEFAULT '',
            confidence numeric(3, 2),
            evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX ix_investigation_findings_run_ordinal ON investigation_findings(investigation_id, ordinal);",
        """
        CREATE TABLE investigation_evidence_links (
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            artifact_id bigint NOT NULL REFERENCES evidence_artifacts(id) ON DELETE CASCADE,
            relation text NOT NULL CHECK (relation IN ('collected', 'inherited', 'manual')),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (investigation_id, artifact_id)
        );
        """,
        "CREATE INDEX ix_investigation_evidence_links_artifact ON investigation_evidence_links(artifact_id);",
        "INSERT INTO investigation_evidence_links (investigation_id, artifact_id, relation) SELECT investigation_id, id, 'collected' FROM evidence_artifacts ON CONFLICT DO NOTHING;",
        "UPDATE investigations i SET audit_status = 'unverifiable' WHERE i.status IN ('completed', 'failed') AND NOT EXISTS (SELECT 1 FROM investigation_execution_events e WHERE e.investigation_id = i.id);",
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError("Dynamic investigation graphs are immutable audit data; restore a database backup instead of downgrading.")
