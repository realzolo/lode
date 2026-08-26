"""Transactional smoke checks for security-critical V1 database triggers."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


_BEHAVIOR_SQL = r"""
DO $checks$
DECLARE
    workspace_row workspaces%ROWTYPE;
    connector_id bigint;
    scope_id bigint;
    investigation_id bigint;
    rejected boolean;
BEGIN
    INSERT INTO workspaces (name, ingestion_topic)
    VALUES ('schema-behavior-workspace', 'schema.behavior.alerts')
    RETURNING * INTO workspace_row;

    PERFORM pg_sleep(0.01);
    UPDATE workspaces SET name = 'schema-behavior-workspace-updated'
    WHERE id = workspace_row.id;
    IF (SELECT updated_at FROM workspaces WHERE id = workspace_row.id)
       <= workspace_row.updated_at THEN
        RAISE EXCEPTION 'updated_at trigger did not advance the timestamp';
    END IF;

    rejected := false;
    BEGIN
        INSERT INTO evidence_connectors (
            workspace_id, name, kind, kind_version, config, secret_ciphertext,
            instance_revision, capabilities
        ) VALUES (
            workspace_row.id, 'unsafe', 'https', 1,
            '{"nested":{"password":"plaintext"}}'::jsonb, 'ciphertext', 1,
            ARRAY['test']::text[]
        );
    EXCEPTION WHEN raise_exception THEN
        IF position('ordinary JSON config may not contain credentials' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'secret-free config trigger accepted a credential';
    END IF;

    INSERT INTO evidence_connectors (
        workspace_id, name, kind, kind_version, config, secret_ciphertext,
        instance_revision, capabilities
    ) VALUES (
        workspace_row.id, 'safe', 'https', 1, '{"base_url":"https://example.test"}'::jsonb,
        'ciphertext', 1, ARRAY['test']::text[]
    ) RETURNING id INTO connector_id;

    INSERT INTO evidence_access_scopes (
        connector_id, allowed_languages, scope_config, schema_catalog,
        schema_catalog_revision, read_policy_revision, execution_budget_policy,
        normalization_policy_revision, revision
    ) VALUES (
        connector_id, ARRAY['https']::text[], '{}'::jsonb, '{}'::jsonb,
        1, 1, '{}'::jsonb, 1, 1
    ) RETURNING id INTO scope_id;

    rejected := false;
    BEGIN
        UPDATE evidence_access_scopes SET revision = 2 WHERE id = scope_id;
    EXCEPTION WHEN raise_exception THEN
        IF position('immutable V1 row cannot be changed' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'immutable trigger accepted an update';
    END IF;

    INSERT INTO investigations (
        public_id, workspace_id, trigger_signature_hash, status, result_state,
        window_started_at, window_finished_at, execution_budget, engine_version,
        finished_at, archived_at
    ) VALUES (
        'schema-behavior-investigation', workspace_row.id, repeat('a', 64),
        'completed', 'insufficient', now() - interval '1 minute', now(),
        '{}'::jsonb, 'schema-behavior', now(), now()
    ) RETURNING id INTO investigation_id;

    rejected := false;
    BEGIN
        INSERT INTO investigation_inputs (
            investigation_id, source_type, event, severity, occurred_at,
            error, raw_payload_masked
        ) VALUES (
            investigation_id, 'manual', 'archive-check', 'WARNING', now(),
            '{}'::jsonb, '{}'::jsonb
        );
    EXCEPTION WHEN raise_exception THEN
        IF position('archived investigation is read-only' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'archive trigger accepted a child insert';
    END IF;
END
$checks$;
"""


async def check_schema_behavior(database_url: str) -> dict[str, Any]:
    """Execute security-critical trigger checks and roll back all fixture rows."""

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(_BEHAVIOR_SQL))
            finally:
                await transaction.rollback()

        async with engine.connect() as first, engine.connect() as second:
            first_transaction = await first.begin()
            second_transaction = await second.begin()
            blocked_insert: asyncio.Task[Any] | None = None
            try:
                await first.execute(
                    text(
                        "INSERT INTO workspaces (name, ingestion_topic) "
                        "VALUES ('concurrency-first', 'schema.behavior.concurrent')"
                    )
                )
                blocked_insert = asyncio.create_task(
                    second.execute(
                        text(
                            "INSERT INTO workspaces (name, ingestion_topic) "
                            "VALUES ('concurrency-second', 'schema.behavior.concurrent')"
                        )
                    )
                )
                await asyncio.sleep(0.05)
                if blocked_insert.done():
                    await blocked_insert
                    raise RuntimeError("concurrent unique-key insert did not wait")
                await first_transaction.rollback()
                await asyncio.wait_for(blocked_insert, timeout=2)
            finally:
                if first_transaction.is_active:
                    await first_transaction.rollback()
                if blocked_insert is not None and not blocked_insert.done():
                    blocked_insert.cancel()
                await second_transaction.rollback()
    finally:
        await engine.dispose()

    return {
        "checks": [
            "archive_readonly",
            "concurrent_unique_topic",
            "immutable_rows",
            "secret_free_config",
            "updated_at",
        ],
        "count": 5,
        "rolled_back": True,
    }
