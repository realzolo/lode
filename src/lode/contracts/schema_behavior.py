"""Transactional smoke checks for security-critical database triggers."""

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
    incident_id bigint;
    signal_id bigint;
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
        connector_id, ARRAY['https']::text[], '{}'::jsonb,
        '{"tables":{"public.accounts":{"columns":{"password":{"type":"text"},"token":{"type":"text"}}}}}'::jsonb,
        1, 1,
        jsonb_build_object(
            'max_result_limit', 1000,
            'max_timeout_ms', 5000,
            'max_output_bytes', 1000000,
            'max_total_output_bytes', 20000000,
            'max_native_reads', 8,
            'max_window_seconds', 7200,
            'max_parallel_operations', 1,
            'estimated_cost', 0.0
        ),
        1, 1
    ) RETURNING id INTO scope_id;

    rejected := false;
    BEGIN
        INSERT INTO evidence_access_scopes (
            connector_id, allowed_languages, scope_config, schema_catalog,
            schema_catalog_revision, read_policy_revision, execution_budget_policy,
            normalization_policy_revision, revision
        ) VALUES (
            connector_id, ARRAY['https']::text[], '{}'::jsonb, '{}'::jsonb,
            2, 2, jsonb_build_object('timeout_ms', 5000, 'max_rows', 1000), 1, 2
        );
    EXCEPTION WHEN check_violation THEN
        IF position('execution budget policy is not canonical' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'canonical budget trigger accepted legacy fields';
    END IF;

    rejected := false;
    BEGIN
        INSERT INTO evidence_access_scopes (
            connector_id, allowed_languages, scope_config, schema_catalog,
            schema_catalog_revision, read_policy_revision, execution_budget_policy,
            normalization_policy_revision, revision
        ) VALUES (
            connector_id, ARRAY['https']::text[],
            '{"nested":{"token":"plaintext"}}'::jsonb, '{}'::jsonb,
            2, 1,
            jsonb_build_object(
                'max_result_limit', 1000,
                'max_timeout_ms', 5000,
                'max_output_bytes', 1000000,
                'max_total_output_bytes', 20000000,
                'max_native_reads', 8,
                'max_window_seconds', 7200,
                'max_parallel_operations', 1,
                'estimated_cost', 0.0
            ),
            1, 2
        );
    EXCEPTION WHEN raise_exception THEN
        IF position('ordinary JSON config may not contain credentials' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'secret-free scope trigger accepted a credential';
    END IF;

    rejected := false;
    BEGIN
        UPDATE evidence_access_scopes SET revision = 2 WHERE id = scope_id;
    EXCEPTION WHEN raise_exception THEN
        IF position('immutable row cannot be changed' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'immutable trigger accepted an update';
    END IF;

    INSERT INTO incidents (
        workspace_id, title, severity, first_occurred_at, last_occurred_at,
        state_changed_at
    ) VALUES (
        workspace_row.id, 'Schema behavior failure', 'UNCLASSIFIED',
        now() - interval '1 minute', now(), now()
    ) RETURNING id INTO incident_id;

    INSERT INTO incident_signals (
        workspace_id, schema_version, source_type, source_event_id,
        idempotency_key_hash, signal_kind, observed_at, severity, title, summary,
        fingerprint, error_masked, raw_payload_masked, raw_payload_ciphertext,
        raw_payload_hash
    ) VALUES (
        workspace_row.id, 'incident-signal.v1', 'manual', NULL,
        repeat('a', 64), 'firing', now(), 'UNCLASSIFIED',
        'Schema behavior failure', 'Immutable signal fixture', repeat('b', 64),
        '{}'::jsonb, '{}'::jsonb, 'ciphertext', repeat('c', 64)
    ) RETURNING id INTO signal_id;

    INSERT INTO incident_signal_links (signal_id, incident_id)
    VALUES (signal_id, incident_id);

    rejected := false;
    BEGIN
        UPDATE incident_signals SET summary = 'mutated' WHERE id = signal_id;
    EXCEPTION WHEN raise_exception THEN
        IF position('immutable row cannot be changed' IN SQLERRM) = 0 THEN
            RAISE;
        END IF;
        rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'incident signal trigger accepted an update';
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
            "incident_signal_immutable",
            "concurrent_unique_topic",
            "immutable_rows",
            "secret_free_config",
            "updated_at",
        ],
        "count": 5,
        "rolled_back": True,
    }
