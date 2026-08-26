"""Control-plane ORM tests."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from lode.db import models  # noqa: F401
from lode.db.base import Base


ROOT = Path(__file__).resolve().parents[2]


def _inventory(group: str) -> set[str]:
    manifest = json.loads(
        (ROOT / "contracts" / "v1" / "database" / "tables.json").read_text(encoding="utf-8")
    )
    return set(manifest[group])


def test_final_control_plane_orm_matches_frozen_table_inventory() -> None:
    assert _inventory("control_plane").issubset(Base.metadata.tables)
    assert set(Base.metadata.tables) == (
        _inventory("control_plane") | _inventory("intake") | _inventory("investigation")
    )


def test_removed_control_plane_tables_are_not_registered() -> None:
    removed = {
        "applications",
        "services",
        "application_service_bindings",
        "application_repos",
        "application_integrations",
        "application_architecture_contexts",
        "ai_model_configs",
        "user_application_perms",
    }
    assert removed.isdisjoint(Base.metadata.tables)


def test_provider_credentials_are_separate_from_model_deployments() -> None:
    accounts = Base.metadata.tables["ai_provider_accounts"]
    deployments = Base.metadata.tables["model_deployments"]

    assert "credential_ciphertext" in accounts.c
    assert "credential_ciphertext" not in deployments.c
    assert "api_key" not in deployments.c
    assert "provider_account_id" in deployments.c


def test_workspace_model_binding_has_portfolio_and_budget_constraints() -> None:
    table = Base.metadata.tables["workspace_model_bindings"]
    checks = {constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
    indexes = {index.name: index for index in table.indexes}

    assert {"execution_classes", "allowed_roles", "allowed_data_classes"}.issubset(table.c.keys())
    assert {
        "ck_workspace_model_bindings_execution_classes_nonempty",
        "ck_workspace_model_bindings_allowed_roles_nonempty",
        "ck_workspace_model_bindings_context_utilization_range",
    }.issubset(checks)
    assert indexes["uq_workspace_model_binding_active"].unique
    assert indexes["uq_workspace_model_binding_active"].dialect_options["postgresql"]["where"] is not None


def test_workspace_activation_schema_has_no_repository_or_component_gate() -> None:
    workspace = Base.metadata.tables["workspaces"]

    assert "ingestion_topic" in workspace.c
    assert "model_policy_revision_id" in workspace.c
    assert "primary_component_id" not in workspace.c
    assert "repository_id" not in workspace.c
    assert any(
        isinstance(constraint, UniqueConstraint) and {column.name for column in constraint.columns} == {"ingestion_topic"}
        for constraint in workspace.constraints
    )


def test_repository_build_unit_and_component_identities_are_separate() -> None:
    repository = Base.metadata.tables["git_repositories"]
    binding = Base.metadata.tables["workspace_repository_bindings"]
    build_unit = Base.metadata.tables["build_units"]
    component = Base.metadata.tables["components"]

    assert "workspace_id" not in repository.c or "scope" in repository.c
    assert {"repository_id", "workspace_id", "role"}.issubset(binding.c.keys())
    assert {"repository_binding_id", "source_root", "build_system"}.issubset(build_unit.c.keys())
    assert "repository_id" not in component.c
    assert "service" + "_name" not in component.c


def test_access_scope_is_versioned_and_secret_free() -> None:
    connector = Base.metadata.tables["evidence_connectors"]
    scope = Base.metadata.tables["evidence_access_scopes"]

    assert "secret_ciphertext" in connector.c
    assert "secret_ciphertext" not in scope.c
    assert {
        "allowed_languages",
        "schema_catalog_revision",
        "read_policy_revision",
        "execution_budget_policy",
        "normalization_policy_revision",
        "revision",
    }.issubset(scope.c.keys())


def test_verified_identity_has_database_provenance_gate() -> None:
    for table_name in ("components", "identity_resolutions"):
        table = Base.metadata.tables[table_name]
        sql = " ".join(
            str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        )
        assert "verified" in sql
        assert "provenance" in sql
