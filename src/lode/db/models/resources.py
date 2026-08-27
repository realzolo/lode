"""Repository, resource-understanding, and knowledge-graph models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lode.db.base import Base
from lode.db.models._common import CreatedAtMixin, TimestampMixin, identity_pk


class GitProviderInstance(TimestampMixin, Base):
    """A globally administered GitHub, GitLab, or Gitee integration."""

    __tablename__ = "git_provider_instances"

    id: Mapped[int] = identity_pk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    verification_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="untested")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("kind IN ('github', 'gitlab', 'gitee')", name="kind"),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint(
            "verification_status IN ('untested', 'healthy', 'unavailable')",
            name="verification_status",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        UniqueConstraint("kind", "base_url", name="uq_git_provider_instance_kind_base_url"),
        UniqueConstraint("name", name="uq_git_provider_instance_name"),
    )


class GitAccountConnection(TimestampMixin, Base):
    """A global, reusable read-only connection to one provider account."""

    __tablename__ = "git_account_connections"

    id: Mapped[int] = identity_pk()
    provider_instance_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_provider_instances.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    auth_mode: Mapped[str] = mapped_column(Text, nullable=False)
    external_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_account_login: Mapped[str] = mapped_column(Text, nullable=False)
    account_url: Mapped[str] = mapped_column(Text, nullable=False)
    current_credential_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    verification_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="untested")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    sync_cursor: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("auth_mode IN ('github_app', 'oauth', 'access_token')", name="auth_mode"),
        CheckConstraint("state IN ('active', 'disabled', 'revoked')", name="state"),
        CheckConstraint(
            "verification_status IN ('untested', 'healthy', 'unavailable')",
            name="verification_status",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        ForeignKeyConstraint(
            ["current_credential_revision_id", "id"],
            [
                "git_account_credential_revisions.id",
                "git_account_credential_revisions.account_connection_id",
            ],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_git_account_connections_current_credential_revision",
        ),
        UniqueConstraint(
            "provider_instance_id", "external_account_id", "auth_mode",
            name="uq_git_account_connection_provider_external_mode",
        ),
    )


class GitAccountCredentialRevision(CreatedAtMixin, Base):
    """Immutable encrypted account authorization material."""

    __tablename__ = "git_account_credential_revisions"

    id: Mapped[int] = identity_pk()
    account_connection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_account_connections.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    credential_identity_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint(
            "credential_identity_hash ~ '^[0-9a-f]{64}$'", name="credential_hash_sha256"
        ),
        UniqueConstraint(
            "id", "account_connection_id", name="uq_git_account_credential_revision_identity"
        ),
        UniqueConstraint("account_connection_id", "revision", name="uq_git_account_credential_revision"),
    )


class GitRepository(TimestampMixin, Base):
    __tablename__ = "git_repositories"

    id: Mapped[int] = identity_pk()
    provider_instance_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_provider_instances.id", ondelete="RESTRICT"), nullable=False
    )
    external_repository_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    web_url: Mapped[str] = mapped_column(Text, nullable=False)
    repo_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="git")
    default_branch: Mapped[str] = mapped_column(Text, nullable=False, server_default="main")
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="private")
    archived: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("visibility IN ('public', 'private', 'internal')", name="visibility"),
        UniqueConstraint(
            "provider_instance_id", "external_repository_id",
            name="uq_git_repository_provider_external",
        ),
    )


class GitAccountRepositoryAccess(TimestampMixin, Base):
    __tablename__ = "git_account_repository_access"

    account_connection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_account_connections.id", ondelete="CASCADE"), primary_key=True
    )
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_repositories.id", ondelete="CASCADE"), primary_key=True
    )
    access_level: Mapped[str] = mapped_column(Text, nullable=False, server_default="read")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="available")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("access_level = 'read'", name="read_only"),
        CheckConstraint("state IN ('available', 'lost')", name="state"),
    )


class WorkspaceGitAccountGrant(TimestampMixin, Base):
    __tablename__ = "workspace_git_account_grants"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    account_connection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_account_connections.id", ondelete="RESTRICT"), nullable=False
    )
    repository_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="selected")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("repository_scope IN ('selected', 'all_visible')", name="repository_scope"),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("revision > 0", name="revision_positive"),
        UniqueConstraint("id", "workspace_id", name="uq_workspace_git_account_grant_workspace"),
        UniqueConstraint("workspace_id", "account_connection_id", name="uq_workspace_git_account_grant"),
    )


class WorkspaceGitRepositoryEntitlement(TimestampMixin, Base):
    __tablename__ = "workspace_git_repository_entitlements"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    grant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_repositories.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("revision > 0", name="revision_positive"),
        ForeignKeyConstraint(
            ["grant_id", "workspace_id"],
            ["workspace_git_account_grants.id", "workspace_git_account_grants.workspace_id"],
            ondelete="CASCADE",
            name="fk_workspace_git_repository_entitlements_grant_workspace",
        ),
        UniqueConstraint(
            "id",
            "workspace_id",
            "repository_id",
            name="uq_workspace_git_repository_entitlement_identity",
        ),
        UniqueConstraint("grant_id", "repository_id", name="uq_workspace_git_repository_entitlement"),
    )


class GitAccountSyncJob(TimestampMixin, Base):
    __tablename__ = "git_account_sync_jobs"

    id: Mapped[int] = identity_pk()
    account_connection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_account_connections.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("state IN ('queued', 'running', 'succeeded', 'failed')", name="state"),
        CheckConstraint("attempt >= 0", name="attempt_nonnegative"),
    )


class WorkspaceRepositoryBinding(TimestampMixin, Base):
    __tablename__ = "workspace_repository_bindings"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("git_repositories.id", ondelete="RESTRICT"), nullable=False
    )
    repository_entitlement_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    descriptor_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "role IN ('runtime_source', 'shared_library', 'infrastructure', 'documentation')",
            name="role",
        ),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("descriptor_revision > 0", name="descriptor_revision_positive"),
        CheckConstraint("revision > 0", name="revision_positive"),
        ForeignKeyConstraint(
            ["repository_entitlement_id", "workspace_id", "repository_id"],
            [
                "workspace_git_repository_entitlements.id",
                "workspace_git_repository_entitlements.workspace_id",
                "workspace_git_repository_entitlements.repository_id",
            ],
            ondelete="RESTRICT",
            name="fk_workspace_repo_bindings_entitlement_scope",
        ),
        Index(
            "uq_workspace_repository_binding_active",
            "workspace_id",
            "repository_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )


class RepositoryDescriptor(CreatedAtMixin, Base):
    __tablename__ = "repository_descriptors"

    id: Mapped[int] = identity_pk()
    repository_binding_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspace_repository_bindings.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    descriptor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generator_version: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("repository_binding_id", "revision", name="uq_repository_descriptor_revision"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class BuildUnit(TimestampMixin, Base):
    __tablename__ = "build_units"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    repository_binding_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspace_repository_bindings.id", ondelete="CASCADE"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_root: Mapped[str] = mapped_column(Text, nullable=False)
    build_system: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_paths: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    entrypoints: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    artifact_hints: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    discovery_basis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    identity_status: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    ownership_priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("workspace_id", "stable_key", name="uq_build_unit_stable_key"),
        CheckConstraint("source_root !~ '(^/|(^|/)\\.\\.(/|$)|\\\\)'", name="source_root_relative"),
        CheckConstraint("identity_status IN ('verified', 'provisional', 'ambiguous')", name="identity_status"),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("ownership_priority >= 0", name="ownership_priority_nonnegative"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class Component(TimestampMixin, Base):
    __tablename__ = "components"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    identity_status: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    discovery_basis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    root_provenance_families: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("workspace_id", "stable_key", name="uq_component_stable_key"),
        CheckConstraint(
            "kind IN ('service', 'worker', 'job', 'gateway', 'library_runtime', 'unknown')",
            name="kind",
        ),
        CheckConstraint("identity_status IN ('verified', 'provisional', 'ambiguous')", name="identity_status"),
        CheckConstraint(
            "identity_status <> 'verified' OR cardinality(root_provenance_families) >= 2",
            name="verified_provenance",
        ),
        CheckConstraint("state IN ('active', 'disabled')", name="state"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class ComponentSourceBinding(CreatedAtMixin, Base):
    __tablename__ = "component_source_bindings"

    component_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("components.id", ondelete="CASCADE"), primary_key=True
    )
    build_unit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("build_units.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    path_prefix: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('primary', 'supporting', 'generated', 'contract')", name="role"),
        CheckConstraint("path_prefix !~ '(^/|(^|/)\\.\\.(/|$)|\\\\)'", name="path_prefix_relative"),
    )


class ComponentDescriptor(CreatedAtMixin, Base):
    __tablename__ = "component_descriptors"

    id: Mapped[int] = identity_pk()
    component_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("components.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    descriptor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generator_version: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("component_id", "revision", name="uq_component_descriptor_revision"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class ResourceObservation(CreatedAtMixin, Base):
    __tablename__ = "resource_observations"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    observation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    repository_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("git_repositories.id", ondelete="CASCADE")
    )
    source_revision: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    connector_id: Mapped[int | None] = mapped_column(BigInteger)
    artifact_id: Mapped[int | None] = mapped_column(BigInteger)
    root_provenance_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_family: Mapped[str] = mapped_column(Text, nullable=False)
    trust_class: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_name: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("source_ref", "source_revision", "content_hash", name="uq_resource_observation_source"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256"),
        CheckConstraint(
            "observation_kind IN ('manifest', 'build_unit', 'entrypoint', 'deployment', "
            "'runtime_config', 'log_identity', 'relation_hint')",
            name="observation_kind",
        ),
        CheckConstraint("valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from", name="valid_range"),
        Index("ix_resource_observations_workspace_kind", "workspace_id", "observation_kind"),
    )


class SemanticAnnotation(CreatedAtMixin, Base):
    __tablename__ = "semantic_annotations"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    annotation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    observation_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    model_invocation_id: Mapped[int | None] = mapped_column(BigInteger)
    prompt_revision: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("cardinality(observation_refs) > 0", name="observations_nonempty"),
    )


class IdentityResolution(CreatedAtMixin, Base):
    __tablename__ = "identity_resolutions"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_kind: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_basis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    observation_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    annotation_refs: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False)
    root_provenance_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    validator_version: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    resolution_hash: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "resolution_kind IN ('build_unit', 'component', 'component_source_binding', "
            "'identity_alias', 'relation_extraction_rule')",
            name="resolution_kind",
        ),
        CheckConstraint("status IN ('verified', 'provisional', 'ambiguous', 'superseded')", name="status"),
        CheckConstraint("cardinality(observation_refs) > 0", name="observations_nonempty"),
        CheckConstraint(
            "status <> 'verified' OR cardinality(root_provenance_refs) >= 2",
            name="verified_provenance",
        ),
        CheckConstraint("resolution_hash ~ '^[0-9a-f]{64}$'", name="resolution_hash_sha256"),
        UniqueConstraint("workspace_id", "resolution_hash", name="uq_identity_resolution_hash"),
    )


class ResourceGraphRevision(CreatedAtMixin, Base):
    __tablename__ = "resource_graph_revisions"

    id: Mapped[int] = identity_pk()
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("resource_graph_revisions.id", ondelete="SET NULL")
    )
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    validator_version: Mapped[str] = mapped_column(Text, nullable=False)
    diff: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "revision", name="uq_resource_graph_revision"),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_sha256"),
    )


class ResourceGraphRevisionMember(Base):
    __tablename__ = "resource_graph_revision_members"

    resource_graph_revision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("resource_graph_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    identity_resolution_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("identity_resolutions.id", ondelete="RESTRICT"), primary_key=True
    )
    member_kind: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "member_kind IN ('build_unit', 'component', 'component_source_binding', "
            "'identity_alias', 'relation_extraction_rule')",
            name="member_kind",
        ),
    )
