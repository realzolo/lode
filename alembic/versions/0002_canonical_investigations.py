"""Replace the retired analysis workflow with canonical investigations.

This is an intentional hard cutover. Existing analysis records are removed
rather than translated, because their missing stage/collector provenance would
make a migrated investigation appear auditable when it is not.
"""

from alembic import op
from sqlalchemy import text

revision = "0002_canonical_investigations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = [
        "DROP TABLE IF EXISTS analysis_feedback CASCADE;",
        "DROP TABLE IF EXISTS analysis_recommendations CASCADE;",
        "DROP TABLE IF EXISTS analysis_guidance_uses CASCADE;",
        "DROP TABLE IF EXISTS analysis_guidances CASCADE;",
        "DROP TABLE IF EXISTS evidence_artifacts CASCADE;",
        "DROP TABLE IF EXISTS analysis_jobs CASCADE;",
        "DROP TABLE IF EXISTS analysis_steps CASCADE;",
        "DROP TABLE IF EXISTS experiences CASCADE;",
        "DROP TABLE IF EXISTS analyses CASCADE;",
        """
        CREATE TABLE investigations (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id text NOT NULL UNIQUE,
            application_id bigint NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            alert_id bigint REFERENCES alerts(id) ON DELETE SET NULL,
            incident_id bigint REFERENCES incidents(id) ON DELETE SET NULL,
            trigger_signature text NOT NULL,
            status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'needs_review', 'failed')),
            output_language text NOT NULL CHECK (output_language IN ('en', 'zh')),
            service_name text, environment text, trace_id text, deployment_sha text,
            window_started_at timestamptz NOT NULL, window_finished_at timestamptz NOT NULL,
            scope jsonb NOT NULL DEFAULT '{}'::jsonb,
            conclusion text, confidence numeric(3, 2) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            started_at timestamptz, finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX ix_investigations_application_created ON investigations(application_id, created_at);",
        "CREATE INDEX ix_investigations_incident ON investigations(incident_id);",
        """
        CREATE TABLE investigation_stages (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            stage_type text NOT NULL CHECK (stage_type IN ('ingest', 'plan', 'source', 'observability', 'dependencies', 'reasoning', 'resolution')),
            status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured')),
            order_index integer NOT NULL, input jsonb NOT NULL DEFAULT '{}'::jsonb, output jsonb NOT NULL DEFAULT '{}'::jsonb,
            failure_code text, failure_detail text, started_at timestamptz, finished_at timestamptz,
            UNIQUE(investigation_id, stage_type)
        );
        """,
        """
        CREATE TABLE evidence_collections (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            stage_id bigint NOT NULL REFERENCES investigation_stages(id) ON DELETE CASCADE,
            connector_kind text NOT NULL, connector_id bigint,
            status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'blocked', 'failed', 'not_configured')),
            selector jsonb NOT NULL DEFAULT '{}'::jsonb, config_hash text, collector_version text NOT NULL DEFAULT '1', artifact_count integer NOT NULL DEFAULT 0,
            failure_code text, failure_detail text, metadata jsonb NOT NULL DEFAULT '{}'::jsonb, started_at timestamptz, finished_at timestamptz
        );
        """,
        "CREATE INDEX ix_evidence_collections_investigation ON evidence_collections(investigation_id, stage_id);",
        """
        CREATE TABLE evidence_artifacts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            collection_id bigint REFERENCES evidence_collections(id) ON DELETE SET NULL,
            artifact_type text NOT NULL CHECK (artifact_type IN ('alert', 'source_file', 'source_diff', 'log', 'metric', 'trace', 'dependency', 'database')),
            source_kind text NOT NULL, source_id bigint, locator text, content_hash text NOT NULL, redacted_excerpt text NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb, collected_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX ix_evidence_artifacts_investigation ON evidence_artifacts(investigation_id, collection_id);",
        """
        CREATE TABLE source_revisions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            repo_id bigint REFERENCES git_repos(id) ON DELETE SET NULL,
            role text NOT NULL CHECK (role IN ('incident', 'latest')), requested_ref text, resolved_sha text, origin_url text NOT NULL,
            status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'resolved', 'failed', 'not_configured')),
            failure_detail text, collected_at timestamptz NOT NULL DEFAULT now(), UNIQUE(investigation_id, repo_id, role)
        );
        """,
        """
        CREATE TABLE hypotheses (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            rank integer NOT NULL, status text NOT NULL CHECK (status IN ('confirmed', 'suspected', 'rejected', 'unknown')),
            text text NOT NULL, confidence numeric(3, 2) NOT NULL CHECK (confidence >= 0 AND confidence <= 1), evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb
        );
        """,
        "CREATE INDEX ix_hypotheses_investigation_rank ON hypotheses(investigation_id, rank);",
        """
        CREATE TABLE remediation_plans (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            investigation_id bigint NOT NULL UNIQUE REFERENCES investigations(id) ON DELETE CASCADE,
            risk_level text NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')), summary text NOT NULL,
            evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb, preconditions jsonb NOT NULL DEFAULT '[]'::jsonb,
            steps jsonb NOT NULL DEFAULT '[]'::jsonb, verification jsonb NOT NULL DEFAULT '[]'::jsonb,
            rollback jsonb NOT NULL DEFAULT '[]'::jsonb, agent_prompt text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        """
        CREATE TABLE investigation_jobs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id text NOT NULL UNIQUE, incident_id bigint NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            investigation_id bigint NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'retry_wait', 'succeeded', 'dead')),
            priority integer NOT NULL DEFAULT 0, attempt integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 5,
            available_at timestamptz NOT NULL DEFAULT now(), lease_owner text, lease_expires_at timestamptz,
            last_error_code text, last_error_detail text, started_at timestamptz, finished_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX ix_investigation_jobs_available ON investigation_jobs(status, available_at, priority, created_at);",
        "CREATE UNIQUE INDEX uq_investigation_jobs_active_incident ON investigation_jobs(incident_id) WHERE status IN ('queued', 'running', 'retry_wait');",
        """
        CREATE TABLE evidence_connectors (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            name text NOT NULL, kind text NOT NULL CHECK (kind IN ('loki', 'prometheus', 'tempo', 'postgres', 'redis', 'kafka', 'clickhouse')),
            state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'disabled')), config jsonb NOT NULL DEFAULT '{}'::jsonb,
            secret_ref text, diagnostic_profile jsonb NOT NULL DEFAULT '{}'::jsonb,
            collection_budget_seconds integer NOT NULL DEFAULT 15 CHECK (collection_budget_seconds BETWEEN 1 AND 60),
            created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
        );
        """,
        "CREATE INDEX ix_evidence_connectors_application ON evidence_connectors(application_id, state);",
        "CREATE TRIGGER trg_investigations_updated_at BEFORE UPDATE ON investigations FOR EACH ROW EXECUTE FUNCTION set_updated_at();",
        "CREATE TRIGGER trg_evidence_connectors_updated_at BEFORE UPDATE ON evidence_connectors FOR EACH ROW EXECUTE FUNCTION set_updated_at();",
    ]
    for statement in statements:
        op.execute(text(statement))


def downgrade() -> None:
    raise RuntimeError("The canonical-investigations cutover is intentionally non-reversible; restore V1 from backup.")
