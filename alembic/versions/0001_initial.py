"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-24

Creates the full Lode schema in its current shape. This project is still in a
fresh initialization phase, so the historical incremental migrations have been
collapsed into this single baseline without compatibility shims.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$;
        """,
        """
        CREATE TABLE users (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            email text NOT NULL,
            name text NOT NULL DEFAULT '',
            password_hash text,
            role text NOT NULL DEFAULT 'user',
            status text NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_users_email UNIQUE (email),
            CONSTRAINT ck_users_role CHECK (role IN ('admin', 'user')),
            CONSTRAINT ck_users_status CHECK (status IN ('pending', 'active', 'disabled'))
        );
        """,
        """
        CREATE TABLE invites (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            email text NOT NULL,
            token text NOT NULL,
            invited_by bigint NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_invites_token UNIQUE (token),
            CONSTRAINT fk_invites_invited_by
                FOREIGN KEY (invited_by) REFERENCES users (id),
            CONSTRAINT ck_invites_status CHECK (status IN ('pending', 'accepted', 'revoked'))
        );
        """,
        """
        CREATE TABLE ai_model_configs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            provider text NOT NULL,
            base_url text NOT NULL,
            api_key_ref text NOT NULL,
            model text NOT NULL,
            is_default boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_ai_model_configs_provider CHECK (provider IN ('openai', 'anthropic'))
        );
        """,
        """
        CREATE UNIQUE INDEX ux_ai_model_configs_default
            ON ai_model_configs (is_default) WHERE is_default;
        """,
        """
        CREATE TABLE platform_settings (
            key text PRIMARY KEY,
            value text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_platform_settings_ai_output_language
                CHECK (key <> 'ai_output_language' OR value IN ('en', 'zh'))
        );
        """,
        """
        CREATE TABLE applications (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name text NOT NULL,
            created_by bigint,
            model_config_id bigint,
            ingestion_state text NOT NULL DEFAULT 'draft',
            ingestion_version integer NOT NULL DEFAULT 0,
            ingestion_start_position text,
            ingestion_started_at timestamptz,
            ingestion_paused_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_applications_created_by
                FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_applications_model_config_id
                FOREIGN KEY (model_config_id) REFERENCES ai_model_configs (id) ON DELETE SET NULL,
            CONSTRAINT ck_applications_ingestion_state
                CHECK (ingestion_state IN ('draft', 'active', 'paused')),
            CONSTRAINT ck_applications_ingestion_start_position
                CHECK (ingestion_start_position IS NULL OR ingestion_start_position IN ('earliest', 'latest'))
        );
        """,
        """
        CREATE TABLE user_application_perms (
            user_id bigint NOT NULL,
            application_id bigint NOT NULL,
            perm text NOT NULL DEFAULT 'read',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_application_perms PRIMARY KEY (user_id, application_id),
            CONSTRAINT fk_user_application_perms_user_id
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT fk_user_application_perms_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_user_application_perms_perm CHECK (perm IN ('read', 'analyze', 'admin'))
        );
        """,
        """
        CREATE TABLE git_credentials (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            auth_type text NOT NULL DEFAULT 'ssh',
            username text NOT NULL DEFAULT '',
            secret_ref text NOT NULL,
            readonly boolean NOT NULL DEFAULT true,
            note text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_git_credentials_auth_type CHECK (auth_type IN ('ssh', 'https'))
        );
        """,
        """
        CREATE TABLE git_repos (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name text NOT NULL,
            repo_url text NOT NULL,
            default_branch text NOT NULL DEFAULT 'main',
            scope text NOT NULL DEFAULT 'global',
            application_id bigint,
            repo_type text NOT NULL DEFAULT 'other',
            credential_id bigint,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_git_repos_scope CHECK (scope IN ('global', 'application')),
            CONSTRAINT ck_git_repos_scope_application CHECK (
                (scope = 'global' AND application_id IS NULL) OR
                (scope = 'application' AND application_id IS NOT NULL)
            ),
            CONSTRAINT fk_git_repos_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT fk_git_repos_credential_id
                FOREIGN KEY (credential_id) REFERENCES git_credentials (id) ON DELETE SET NULL
        );
        """,
        """
        CREATE UNIQUE INDEX ux_git_repos_global_repo_url
            ON git_repos (repo_url) WHERE scope = 'global';
        """,
        """
        CREATE UNIQUE INDEX ux_git_repos_app_repo_url
            ON git_repos (application_id, repo_url) WHERE scope = 'application';
        """,
        """
        CREATE TABLE application_kafka (
            application_id bigint NOT NULL,
            topic text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_application_kafka PRIMARY KEY (application_id),
            CONSTRAINT uq_application_kafka_topic UNIQUE (topic),
            CONSTRAINT fk_application_kafka_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE
        );
        """,
        """
        CREATE TABLE application_ingestion_runtime (
            application_id bigint PRIMARY KEY REFERENCES applications(id) ON DELETE CASCADE,
            observed_state text NOT NULL DEFAULT 'idle',
            observed_version integer NOT NULL DEFAULT 0,
            consumer_id text,
            assigned_partitions integer NOT NULL DEFAULT 0,
            backlog bigint,
            last_heartbeat_at timestamptz,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_application_ingestion_runtime_observed_state
                CHECK (observed_state IN ('idle', 'starting', 'listening', 'paused', 'error'))
        );
        """,
        """
        CREATE TABLE application_ingestion_offsets (
            application_id bigint NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            ingestion_version integer NOT NULL,
            topic text NOT NULL,
            partition integer NOT NULL,
            start_position text NOT NULL,
            target_offset bigint NOT NULL,
            initialized_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_application_ingestion_offsets
                PRIMARY KEY (application_id, ingestion_version, topic, partition),
            CONSTRAINT ck_application_ingestion_offsets_start_position
                CHECK (start_position IN ('earliest', 'latest'))
        );
        """,
        "CREATE INDEX ix_application_ingestion_offsets_topic ON application_ingestion_offsets (topic, partition);",
        """
        CREATE TABLE application_repos (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            repo_id bigint NOT NULL,
            description text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_app_repo UNIQUE (application_id, repo_id),
            CONSTRAINT fk_application_repos_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT fk_application_repos_repo_id
                FOREIGN KEY (repo_id) REFERENCES git_repos (id) ON DELETE RESTRICT
        );
        """,
        """
        CREATE TABLE application_descriptions (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            description_type text NOT NULL DEFAULT 'deploy',
            content text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_application_descriptions_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_application_descriptions_description_type
                CHECK (description_type IN ('deploy', 'other'))
        );
        """,
        """
        CREATE TABLE db_sources (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            name text NOT NULL,
            description text NOT NULL DEFAULT '',
            conn_secret_ref text,
            host text,
            port integer,
            database text,
            username text,
            password text,
            sslmode text,
            allowed_tables jsonb NOT NULL DEFAULT '[]'::jsonb,
            sensitive_columns jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_db_sources_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_db_sources_secure_connection CHECK (
                (conn_secret_ref IS NOT NULL
                 AND conn_secret_ref ~ '^env://[A-Za-z_][A-Za-z0-9_]*$'
                 AND host IS NULL AND port IS NULL AND database IS NULL
                 AND username IS NULL AND password IS NULL AND sslmode IS NULL)
                OR
                (conn_secret_ref IS NULL AND host IS NOT NULL AND database IS NOT NULL
                 AND sslmode = 'verify-full')
            )
        );
        """,
        """
        CREATE TABLE application_integrations (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            name text NOT NULL,
            kind text NOT NULL,
            config jsonb NOT NULL DEFAULT '{}'::jsonb,
            secret_ref text NOT NULL,
            state text NOT NULL DEFAULT 'active',
            readonly_verified_at timestamptz,
            last_collected_at timestamptz,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_application_integrations_kind
                CHECK (kind IN ('redis', 'kafka', 'clickhouse')),
            CONSTRAINT ck_application_integrations_state
                CHECK (state IN ('active', 'disabled'))
        );
        """,
        "CREATE INDEX ix_application_integrations_application_id ON application_integrations(application_id);",
        """
        CREATE TABLE alerts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            dedupe_key text NOT NULL,
            application_id bigint NOT NULL,
            topic text NOT NULL,
            title text NOT NULL DEFAULT '',
            level text NOT NULL,
            alert_id text,
            occurred_at timestamptz,
            dedupe_ttl_seconds integer,
            error_message text NOT NULL DEFAULT '',
            fields jsonb NOT NULL DEFAULT '{}'::jsonb,
            error_log jsonb,
            raw_payload jsonb NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_alerts_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_alerts_level CHECK (level IN ('CRITICAL', 'WARNING'))
        );
        """,
        "CREATE INDEX ix_alerts_dedupe_key ON alerts (dedupe_key);",
        "CREATE INDEX ix_alerts_application_id ON alerts (application_id);",
        "CREATE INDEX ix_alerts_fields ON alerts USING gin (fields jsonb_path_ops);",
        """
        CREATE TABLE analyses (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id text NOT NULL,
            dedupe_key text NOT NULL,
            application_id bigint NOT NULL,
            alert_id bigint,
            incident_id bigint,
            job_id bigint,
            engine_version text,
            failure_code text,
            failure_detail text,
            status text NOT NULL DEFAULT 'pending',
            conclusion text,
            confidence numeric(3, 2),
            evidence jsonb,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_analyses_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT fk_analyses_alert_id
                FOREIGN KEY (alert_id) REFERENCES alerts (id) ON DELETE SET NULL,
            CONSTRAINT uq_analyses_public_id UNIQUE (public_id),
            CONSTRAINT ck_analyses_status
                CHECK (status IN ('pending', 'running', 'completed', 'needs_review', 'failed', 'canceled')),
            CONSTRAINT ck_analyses_confidence
                CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
        );
        """,
        "CREATE INDEX ix_analyses_dedupe_key ON analyses (dedupe_key);",
        "CREATE INDEX ix_analyses_application_id ON analyses (application_id);",
        "CREATE INDEX ix_analyses_incident_id ON analyses (incident_id);",
        """
        CREATE TABLE analysis_steps (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            analysis_id bigint NOT NULL,
            node_type text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            input jsonb,
            output jsonb,
            order_index integer NOT NULL DEFAULT 0,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_analysis_steps_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
            CONSTRAINT ck_analysis_steps_node_type
                CHECK (node_type IN ('receive', 'git_sync', 'context', 'service_snapshot', 'experience', 'ai_analysis', 'conclusion')),
            CONSTRAINT ck_analysis_steps_status
                CHECK (status IN ('pending', 'running', 'completed', 'degraded', 'failed', 'skipped')),
            CONSTRAINT uq_analysis_steps_analysis_node UNIQUE (analysis_id, node_type)
        );
        """,
        "CREATE INDEX ix_analysis_steps_analysis_id ON analysis_steps (analysis_id);",
        """
        CREATE TABLE experiences (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            trigger_signature text NOT NULL,
            content text NOT NULL,
            embedding real[],
            source_analysis_id bigint,
            is_valid boolean NOT NULL DEFAULT true,
            expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_experiences_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT fk_experiences_source_analysis_id
                FOREIGN KEY (source_analysis_id) REFERENCES analyses (id) ON DELETE SET NULL
        );
        """,
        "CREATE INDEX ix_experiences_application_id ON experiences (application_id);",
        "CREATE INDEX ix_experiences_trigger_signature ON experiences (trigger_signature);",
        "CREATE INDEX ix_experiences_expires_at ON experiences (expires_at);",
        """
        CREATE TABLE ingestion_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint,
            topic text NOT NULL,
            partition integer,
            "offset" bigint,
            producer_event_id text,
            payload_hash text,
            trace_id text,
            status text NOT NULL DEFAULT 'accepted',
            received_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_ingestion_events_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_ingestion_events_status
                CHECK (status IN ('accepted', 'dlq', 'unassigned'))
        );
        """,
        """
        CREATE UNIQUE INDEX uq_ingestion_events_topic_partition_offset
            ON ingestion_events (topic, partition, "offset");
        """,
        """
        CREATE TABLE incidents (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id uuid NOT NULL,
            application_id bigint NOT NULL,
            dedupe_key text NOT NULL,
            state text NOT NULL DEFAULT 'open',
            first_alert_id bigint,
            latest_alert_id bigint,
            alert_count integer NOT NULL DEFAULT 0,
            first_seen_at timestamptz,
            last_seen_at timestamptz,
            reanalysis_requested_at timestamptz,
            reanalysis_requested_by bigint,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_incidents_public_id UNIQUE (public_id),
            CONSTRAINT fk_incidents_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT fk_incidents_first_alert_id
                FOREIGN KEY (first_alert_id) REFERENCES alerts (id) ON DELETE SET NULL,
            CONSTRAINT fk_incidents_latest_alert_id
                FOREIGN KEY (latest_alert_id) REFERENCES alerts (id) ON DELETE SET NULL,
            CONSTRAINT fk_incidents_reanalysis_requested_by
                FOREIGN KEY (reanalysis_requested_by) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT ck_incidents_state
                CHECK (state IN ('open', 'resolved', 'suppressed'))
        );
        """,
        "CREATE UNIQUE INDEX uq_incidents_application_id_dedupe_key ON incidents (application_id, dedupe_key);",
        """
        CREATE TABLE analysis_jobs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            public_id uuid NOT NULL,
            incident_id bigint NOT NULL,
            analysis_id bigint,
            trigger text NOT NULL DEFAULT 'ingest',
            status text NOT NULL DEFAULT 'queued',
            priority integer NOT NULL DEFAULT 0,
            attempt integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 5,
            available_at timestamptz NOT NULL DEFAULT now(),
            lease_owner text,
            lease_expires_at timestamptz,
            last_error_code text,
            last_error_detail text,
            requested_by bigint,
            trace_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            finished_at timestamptz,
            CONSTRAINT uq_analysis_jobs_public_id UNIQUE (public_id),
            CONSTRAINT fk_analysis_jobs_incident_id
                FOREIGN KEY (incident_id) REFERENCES incidents (id) ON DELETE CASCADE,
            CONSTRAINT fk_analysis_jobs_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE SET NULL,
            CONSTRAINT fk_analysis_jobs_requested_by
                FOREIGN KEY (requested_by) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT ck_analysis_jobs_status
                CHECK (status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'canceled', 'dead')),
            CONSTRAINT ck_analysis_jobs_attempt CHECK (attempt >= 0)
        );
        """,
        """
        CREATE INDEX ix_analysis_jobs_status_available
            ON analysis_jobs (status, available_at, priority, created_at);
        """,
        """
        CREATE INDEX ix_analysis_jobs_lease_expires
            ON analysis_jobs (lease_expires_at) WHERE status = 'running';
        """,
        """
        CREATE UNIQUE INDEX uq_analysis_jobs_active_incident
            ON analysis_jobs (incident_id)
            WHERE status IN ('queued', 'running', 'retry_wait');
        """,
        """
        CREATE TABLE analysis_guidances (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            incident_id bigint NOT NULL,
            source_analysis_id bigint,
            author_id bigint,
            author text NOT NULL DEFAULT '',
            content text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_analysis_guidances_incident_id
                FOREIGN KEY (incident_id) REFERENCES incidents (id) ON DELETE CASCADE,
            CONSTRAINT fk_analysis_guidances_source_analysis_id
                FOREIGN KEY (source_analysis_id) REFERENCES analyses (id) ON DELETE SET NULL,
            CONSTRAINT fk_analysis_guidances_author_id
                FOREIGN KEY (author_id) REFERENCES users (id) ON DELETE SET NULL
        );
        """,
        "CREATE INDEX ix_analysis_guidances_incident_created ON analysis_guidances (incident_id, created_at);",
        """
        CREATE TABLE analysis_guidance_uses (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            guidance_id bigint NOT NULL,
            analysis_id bigint NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_analysis_guidance_use UNIQUE (guidance_id, analysis_id),
            CONSTRAINT fk_analysis_guidance_uses_guidance_id
                FOREIGN KEY (guidance_id) REFERENCES analysis_guidances (id) ON DELETE CASCADE,
            CONSTRAINT fk_analysis_guidance_uses_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
        );
        """,
        "CREATE INDEX ix_analysis_guidance_uses_analysis_id ON analysis_guidance_uses (analysis_id);",
        """
        CREATE TABLE analysis_recommendations (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            analysis_id bigint NOT NULL,
            summary text NOT NULL,
            risk_level text NOT NULL,
            basis text NOT NULL DEFAULT 'evidence_backed',
            evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            preconditions jsonb NOT NULL DEFAULT '[]'::jsonb,
            steps jsonb NOT NULL DEFAULT '[]'::jsonb,
            verification jsonb NOT NULL DEFAULT '[]'::jsonb,
            rollback jsonb NOT NULL DEFAULT '[]'::jsonb,
            owner_role text,
            prompt_markdown text NOT NULL,
            engine_version text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_analysis_recommendations_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
            CONSTRAINT uq_analysis_recommendations_analysis_id UNIQUE (analysis_id),
            CONSTRAINT ck_analysis_recommendations_risk_level
                CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
            CONSTRAINT ck_analysis_recommendations_basis
                CHECK (basis IN ('evidence_backed', 'safety_fallback'))
        );
        """,
        "CREATE INDEX ix_analysis_recommendations_analysis_id ON analysis_recommendations (analysis_id);",
        """
        CREATE TABLE analysis_feedback (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            analysis_id bigint NOT NULL,
            actor_id bigint NOT NULL,
            target text NOT NULL,
            value text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_analysis_feedback_analysis_actor_target
                UNIQUE (analysis_id, actor_id, target),
            CONSTRAINT fk_analysis_feedback_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
            CONSTRAINT fk_analysis_feedback_actor_id
                FOREIGN KEY (actor_id) REFERENCES users (id) ON DELETE CASCADE,
            CONSTRAINT ck_analysis_feedback_target
                CHECK (target IN ('remediation', 'agent_prompt')),
            CONSTRAINT ck_analysis_feedback_value
                CHECK (value IN ('useful', 'not_useful'))
        );
        """,
        "CREATE INDEX ix_analysis_feedback_analysis_id ON analysis_feedback (analysis_id);",
        """
        ALTER TABLE analyses
            ADD CONSTRAINT fk_analyses_incident_id
                FOREIGN KEY (incident_id) REFERENCES incidents (id) ON DELETE SET NULL,
            ADD CONSTRAINT fk_analyses_job_id
                FOREIGN KEY (job_id) REFERENCES analysis_jobs (id) ON DELETE SET NULL;
        """,
        """
        CREATE TABLE evidence_artifacts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            analysis_id bigint NOT NULL,
            artifact_type text NOT NULL,
            source_kind text,
            source_id bigint,
            locator text,
            content_hash text,
            redacted_excerpt text,
            metadata jsonb,
            retention_until timestamptz,
            collected_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_evidence_artifacts_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
            CONSTRAINT ck_evidence_artifacts_type
                CHECK (artifact_type IN ('git_file', 'git_diff', 'db_query', 'deploy',
                                        'alert_payload', 'service_snapshot', 'operator_guidance'))
        );
        """,
        "CREATE INDEX ix_evidence_artifacts_analysis_id ON evidence_artifacts (analysis_id);",
        """
        CREATE TABLE audit_events (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            actor_id bigint,
            actor_email text,
            action text NOT NULL,
            target_type text,
            target_id text,
            application_id bigint,
            request_id text,
            trace_id text,
            result text NOT NULL DEFAULT 'ok',
            detail jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_audit_events_actor_id
                FOREIGN KEY (actor_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_audit_events_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE SET NULL
        );
        """,
        "CREATE INDEX ix_audit_events_created_at ON audit_events (created_at);",
        "CREATE INDEX ix_audit_events_actor_id ON audit_events (actor_id);",
        "CREATE INDEX ix_audit_events_action ON audit_events (action);",
        """
        CREATE TABLE dead_letters (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            kind text NOT NULL,
            topic text NOT NULL,
            application_id bigint,
            dedupe_key text,
            payload jsonb,
            reason text,
            partition integer,
            "offset" bigint,
            replayed boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_dead_letters_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE SET NULL
        );
        """,
        "CREATE INDEX ix_dead_letters_kind ON dead_letters (kind);",
        "CREATE INDEX ix_dead_letters_created_at ON dead_letters (created_at);",
        """
        CREATE UNIQUE INDEX uq_dead_letters_source
            ON dead_letters (topic, partition, "offset", kind)
            WHERE partition IS NOT NULL AND "offset" IS NOT NULL;
        """,
        """
        CREATE FUNCTION reject_evidence_artifact_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evidence artifacts are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """,
        """
        CREATE TRIGGER trg_evidence_artifacts_immutable
        BEFORE UPDATE ON evidence_artifacts
        FOR EACH ROW EXECUTE FUNCTION reject_evidence_artifact_update();
        """,
        """
        CREATE FUNCTION reject_integration_config_secret() RETURNS trigger AS $$
        BEGIN
            IF jsonb_path_exists(NEW.config, '$.**.password')
               OR jsonb_path_exists(NEW.config, '$.**.passwd')
               OR jsonb_path_exists(NEW.config, '$.**.secret')
               OR jsonb_path_exists(NEW.config, '$.**.token')
               OR jsonb_path_exists(NEW.config, '$.**.api_key')
               OR jsonb_path_exists(NEW.config, '$.**.access_key') THEN
                RAISE EXCEPTION 'integration config may not contain credentials';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """,
        """
        CREATE TRIGGER trg_application_integrations_secret_free_config
        BEFORE INSERT OR UPDATE OF config ON application_integrations
        FOR EACH ROW EXECUTE FUNCTION reject_integration_config_secret();
        """,
        """
        DO $$
        DECLARE
            t text;
        BEGIN
            FOR t IN SELECT unnest(ARRAY[
                'users',
                'invites',
                'ai_model_configs',
                'applications',
                'application_ingestion_runtime',
                'user_application_perms',
                'git_credentials',
                'git_repos',
                'application_kafka',
                'application_repos',
                'application_descriptions',
                'db_sources',
                'application_integrations',
                'platform_settings',
                'analyses',
                'analysis_steps',
                'experiences',
                'incidents',
                'dead_letters',
                'analysis_recommendations',
                'analysis_feedback'
            ])
            LOOP
                EXECUTE format(
                    'CREATE TRIGGER trg_%1$s_updated_at BEFORE UPDATE ON %1$s '
                    'FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t);
            END LOOP;
        END $$;
        """,
    ]

    for stmt in statements:
        op.execute(text(stmt))


def downgrade() -> None:
    tables = [
        "dead_letters",
        "audit_events",
        "evidence_artifacts",
        "analysis_feedback",
        "analysis_recommendations",
        "analysis_jobs",
        "incidents",
        "ingestion_events",
        "experiences",
        "analysis_steps",
        "analysis_guidance_uses",
        "analysis_guidances",
        "analyses",
        "alerts",
        "db_sources",
        "application_integrations",
        "application_descriptions",
        "application_repos",
        "application_kafka",
        "application_ingestion_offsets",
        "application_ingestion_runtime",
        "git_repos",
        "git_credentials",
        "user_application_perms",
        "applications",
        "platform_settings",
        "ai_model_configs",
        "invites",
        "users",
    ]
    for table_name in tables:
        op.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE;"))
    op.execute(text("DROP FUNCTION IF EXISTS set_updated_at() CASCADE;"))
