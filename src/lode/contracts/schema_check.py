"""Verify a migrated PostgreSQL database against the frozen current inventory."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from lode.contracts.checks import CONTRACT_ROOT


class SchemaInvariantError(RuntimeError):
    """Raised when a migrated database differs from the frozen current contract."""


def _load(relative: str) -> dict[str, Any]:
    path = CONTRACT_ROOT / relative
    return json.loads(path.read_text(encoding="utf-8"))


def expected_schema() -> tuple[set[str], dict[str, set[str]]]:
    tables = _load("database/tables.json")
    invariants = _load("database/invariants.json")
    inventory = set(tables["control_plane"] + tables["intake"] + tables["investigation"])

    expected_triggers: dict[str, set[str]] = defaultdict(set)
    categories = {
        "immutable_tables": "immutable",
        "updated_at_tables": "updated_at",
    }
    for field, suffix in categories.items():
        names = invariants[field]
        if names != sorted(names) or len(names) != len(set(names)):
            raise SchemaInvariantError(f"{field} must be sorted and unique")
        unknown = set(names) - inventory
        if unknown:
            raise SchemaInvariantError(f"{field} references unknown tables: {sorted(unknown)}")
        for table_name in names:
            expected_triggers[table_name].add(f"trg_{table_name}_{suffix}")

    required = invariants["required_triggers"]
    if list(required) != sorted(required):
        raise SchemaInvariantError("required_triggers keys must be sorted")
    for table_name, trigger_names in required.items():
        if table_name not in inventory:
            raise SchemaInvariantError(f"required trigger references unknown table: {table_name}")
        if trigger_names != sorted(trigger_names) or len(trigger_names) != len(set(trigger_names)):
            raise SchemaInvariantError(
                f"required triggers for {table_name} must be sorted and unique"
            )
        expected_triggers[table_name].update(trigger_names)

    return inventory, dict(expected_triggers)


async def check_schema(database_url: str) -> dict[str, Any]:
    """Return a deterministic summary or raise on any schema mismatch."""

    inventory, expected_triggers = expected_schema()
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            actual_tables = set(
                (
                    await connection.execute(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                        )
                    )
                ).scalars()
            )
            trigger_rows = (
                await connection.execute(
                    text(
                        "SELECT c.relname, t.tgname "
                        "FROM pg_catalog.pg_trigger AS t "
                        "JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid "
                        "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
                    )
                )
            ).all()
            workspace_policy_fks = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_constraint AS con "
                        "JOIN pg_catalog.pg_class AS src ON src.oid = con.conrelid "
                        "JOIN pg_catalog.pg_class AS dst ON dst.oid = con.confrelid "
                        "JOIN pg_catalog.pg_namespace AS n ON n.oid = src.relnamespace "
                        "WHERE n.nspname = 'public' AND con.contype = 'f' "
                        "AND con.conname = "
                        "'fk_workspaces_model_policy_revision_id_model_policy_revisions' "
                        "AND src.relname = 'workspaces' "
                        "AND dst.relname = 'model_policy_revisions'"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    missing_tables = inventory - actual_tables
    extra_tables = actual_tables - inventory
    if missing_tables or extra_tables:
        raise SchemaInvariantError(
            f"table inventory mismatch; missing={sorted(missing_tables)}, extra={sorted(extra_tables)}"
        )

    actual_triggers: dict[str, set[str]] = defaultdict(set)
    for table_name, trigger_name in trigger_rows:
        actual_triggers[table_name].add(trigger_name)

    missing_triggers = {
        table_name: sorted(names - actual_triggers.get(table_name, set()))
        for table_name, names in expected_triggers.items()
        if names - actual_triggers.get(table_name, set())
    }
    if missing_triggers:
        raise SchemaInvariantError(f"required triggers are missing: {missing_triggers}")
    unexpected_triggers = {
        table_name: sorted(names - expected_triggers.get(table_name, set()))
        for table_name, names in actual_triggers.items()
        if names - expected_triggers.get(table_name, set())
    }
    if unexpected_triggers:
        raise SchemaInvariantError(f"unexpected triggers are present: {unexpected_triggers}")
    if workspace_policy_fks != 1:
        raise SchemaInvariantError("Workspace current policy foreign keys are missing")

    return {
        "foreign_keys_checked": 1,
        "required_trigger_count": sum(len(names) for names in expected_triggers.values()),
        "table_count": len(actual_tables),
        "trigger_count": len(trigger_rows),
    }


def contract_path() -> Path:
    """Expose the contract path for diagnostics without duplicating root logic."""

    return CONTRACT_ROOT / "database" / "invariants.json"
