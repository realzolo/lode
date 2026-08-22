"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-21

Creates the full Lode schema (16 tables) following PostgreSQL
best practices: lowercase snake_case names, plural table names, bigint
GENERATED ALWAYS AS IDENTITY primary keys, timestamptz with now() defaults,
jsonb for semi-structured data, explicit CHECK constraints, partial unique
indexes for "exactly one default" per scope, GIN index on alert fields, and a
single updated_at trigger function applied to every mutable table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that carry an `updated_at` column maintained by the trigger.
_UPDATED_AT_TABLES = [
    "users",
    "invites",
    "applications",
    "git_credentials",
    "git_repos",
    "ai_model_configs",
    "application_repos",
    "preset_prompts",
    "db_sources",
    "alerts",
    "analyses",
    "analysis_hints",
    "memories",
]


def upgrade() -> None:
    statements = [
        # ------------------------------------------------------------------
        # Shared trigger function: keep `updated_at` current on every UPDATE.
        # ------------------------------------------------------------------
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$;
        """,
        # ------------------------------------------------------------------
        # users
        # ------------------------------------------------------------------
        """
        CREATE TABLE users (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            email text NOT NULL,
            name text NOT NULL DEFAULT '',
            role text NOT NULL DEFAULT 'user',
            status text NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_users_email UNIQUE (email),
            CONSTRAINT ck_users_role CHECK (role IN ('admin', 'user')),
            CONSTRAINT ck_users_status CHECK (status IN ('pending', 'active', 'disabled'))
        );
        """,
        # ------------------------------------------------------------------
        # invites
        # ------------------------------------------------------------------
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
            CONSTRAINT fk_invites_invited_by FOREIGN KEY (invited_by) REFERENCES users (id),
            CONSTRAINT ck_invites_status CHECK (status IN ('pending', 'accepted', 'revoked'))
        );
        """,
        # ------------------------------------------------------------------
        # applications (isolation unit; one Kafka topic each)
        # ------------------------------------------------------------------
        """
        CREATE TABLE applications (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name text NOT NULL,
            created_by bigint,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_applications_created_by
                FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL
        );
        """,
        # ------------------------------------------------------------------
        # user_application_perms (composite PK)
        # ------------------------------------------------------------------
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
        # ------------------------------------------------------------------
        # git_credentials (global read-only git account)
        # ------------------------------------------------------------------
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
        # ------------------------------------------------------------------
        # git_repos (global repository registry)
        # ------------------------------------------------------------------
        """
        CREATE TABLE git_repos (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name text NOT NULL,
            repo_url text NOT NULL,
            default_branch text NOT NULL DEFAULT 'main',
            credential_id bigint,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_git_repos_repo_url UNIQUE (repo_url),
            CONSTRAINT fk_git_repos_credential_id
                FOREIGN KEY (credential_id) REFERENCES git_credentials (id) ON DELETE SET NULL
        );
        """,
        # ------------------------------------------------------------------
        # ai_model_configs (global default or per-application override)
        # ------------------------------------------------------------------
        """
        CREATE TABLE ai_model_configs (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            scope text NOT NULL DEFAULT 'global',
            application_id bigint,
            provider text NOT NULL,
            base_url text NOT NULL,
            api_key_ref text NOT NULL,
            model text NOT NULL,
            is_default boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_ai_model_configs_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_ai_model_configs_scope CHECK (scope IN ('global', 'application')),
            CONSTRAINT ck_ai_model_configs_provider CHECK (provider IN ('openai', 'anthropic')),
            CONSTRAINT ck_ai_model_configs_scope_application
                CHECK (scope = 'application' OR application_id IS NULL)
        );
        """,
        """
        CREATE UNIQUE INDEX ux_ai_model_configs_global_default
            ON ai_model_configs (scope) WHERE scope = 'global' AND is_default;
        """,
        """
        CREATE UNIQUE INDEX ux_ai_model_configs_app_default
            ON ai_model_configs (application_id) WHERE scope = 'application' AND is_default;
        """,
        # ------------------------------------------------------------------
        # application_kafka (topic binding, one row per application)
        # ------------------------------------------------------------------
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
        # ------------------------------------------------------------------
        # application_repos (selected repos + per-repo description)
        # ------------------------------------------------------------------
        """
        CREATE TABLE application_repos (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            repo_id bigint NOT NULL,
            description text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_application_repos_app_repo UNIQUE (application_id, repo_id),
            CONSTRAINT fk_application_repos_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT fk_application_repos_repo_id
                FOREIGN KEY (repo_id) REFERENCES git_repos (id) ON DELETE RESTRICT
        );
        """,
        # ------------------------------------------------------------------
        # preset_prompts (e.g. deployment description)
        # ------------------------------------------------------------------
        """
        CREATE TABLE preset_prompts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            type text NOT NULL DEFAULT 'deploy',
            content text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_preset_prompts_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_preset_prompts_type CHECK (type IN ('deploy', 'other'))
        );
        """,
        # ------------------------------------------------------------------
        # db_sources (read-only data sources, table whitelist as jsonb)
        # ------------------------------------------------------------------
        """
        CREATE TABLE db_sources (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            name text NOT NULL,
            conn_secret_ref text NOT NULL,
            allowed_tables jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_db_sources_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE
        );
        """,
        # ------------------------------------------------------------------
        # alerts (one row per consumed Kafka message)
        # ------------------------------------------------------------------
        """
        CREATE TABLE alerts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            dedupe_key text NOT NULL,
            application_id bigint NOT NULL,
            topic text NOT NULL,
            title text NOT NULL DEFAULT '',
            level text NOT NULL,
            env text NOT NULL,
            error_message text NOT NULL DEFAULT '',
            fields jsonb NOT NULL DEFAULT '{}'::jsonb,
            raw_payload jsonb NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_alerts_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT ck_alerts_level CHECK (level IN ('CRITICAL', 'WARNING'))
        );
        """,
        """
        CREATE INDEX ix_alerts_dedupe_key ON alerts (dedupe_key);
        """,
        """
        CREATE INDEX ix_alerts_application_id ON alerts (application_id);
        """,
        """
        CREATE INDEX ix_alerts_fields ON alerts USING gin (fields jsonb_path_ops);
        """,
        # ------------------------------------------------------------------
        # analyses
        # ------------------------------------------------------------------
        """
        CREATE TABLE analyses (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            dedupe_key text NOT NULL,
            application_id bigint NOT NULL,
            alert_id bigint,
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
            CONSTRAINT ck_analyses_status
                CHECK (status IN ('pending', 'running', 'completed', 'failed', 'canceled')),
            CONSTRAINT ck_analyses_confidence
                CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
        );
        """,
        """
        CREATE INDEX ix_analyses_dedupe_key ON analyses (dedupe_key);
        """,
        """
        CREATE INDEX ix_analyses_application_id ON analyses (application_id);
        """,
        # ------------------------------------------------------------------
        # analysis_hints (human-in-the-loop)
        # ------------------------------------------------------------------
        """
        CREATE TABLE analysis_hints (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            analysis_id bigint NOT NULL,
            author text NOT NULL DEFAULT '',
            content text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_analysis_hints_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
        );
        """,
        # ------------------------------------------------------------------
        # analysis_steps (workflow nodes)
        # ------------------------------------------------------------------
        """
        CREATE TABLE analysis_steps (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            analysis_id bigint NOT NULL,
            node_type text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            input jsonb,
            output jsonb,
            order_index integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_analysis_steps_analysis_id
                FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE,
            CONSTRAINT ck_analysis_steps_node_type
                CHECK (node_type IN ('receive', 'git_sync', 'context', 'ai_analysis', 'memory', 'conclusion')),
            CONSTRAINT ck_analysis_steps_status
                CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped'))
        );
        """,
        """
        CREATE INDEX ix_analysis_steps_analysis_id ON analysis_steps (analysis_id);
        """,
        # ------------------------------------------------------------------
        # memories (shared, reusable conclusions)
        # ------------------------------------------------------------------
        """
        CREATE TABLE memories (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            application_id bigint NOT NULL,
            trigger_signature text NOT NULL,
            content text NOT NULL,
            source_analysis_id bigint,
            is_valid boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_memories_application_id
                FOREIGN KEY (application_id) REFERENCES applications (id) ON DELETE CASCADE,
            CONSTRAINT fk_memories_source_analysis_id
                FOREIGN KEY (source_analysis_id) REFERENCES analyses (id) ON DELETE SET NULL
        );
        """,
        """
        CREATE INDEX ix_memories_application_id ON memories (application_id);
        """,
        """
        CREATE INDEX ix_memories_trigger_signature ON memories (trigger_signature);
        """,
        # ------------------------------------------------------------------
        # Attach the updated_at trigger to every mutable table.
        # ------------------------------------------------------------------
        """
        DO $$
        DECLARE
            t text;
        BEGIN
            FOR t IN SELECT unnest(ARRAY[
                'users', 'invites', 'applications', 'git_credentials', 'git_repos',
                'ai_model_configs', 'application_repos', 'preset_prompts',
                'db_sources', 'alerts', 'analyses', 'analysis_hints', 'memories'
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
        "memories",
        "analysis_steps",
        "analysis_hints",
        "analyses",
        "alerts",
        "db_sources",
        "preset_prompts",
        "application_repos",
        "application_kafka",
        "ai_model_configs",
        "git_repos",
        "git_credentials",
        "user_application_perms",
        "applications",
        "invites",
        "users",
    ]
    for t in tables:
        op.execute(text(f'DROP TABLE IF EXISTS {t} CASCADE;'))
    op.execute(text("DROP FUNCTION IF EXISTS set_updated_at() CASCADE;"))
