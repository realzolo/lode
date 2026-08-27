"""Freeze repository and model control-plane state in the intake transaction."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.intake import canonical_hash
from lode.crypto import CryptoError, decrypt_secret
from lode.db.models import (
    AIProviderAccount,
    ContextPolicyRevision,
    GitCredential,
    GitRepository,
    InvestigationDescriptorSnapshot,
    InvestigationModelBindingSnapshot,
    InvestigationModelPolicySnapshot,
    InvestigationRepositorySnapshot,
    ModelDeployment,
    ModelPolicyRevision,
    RepositoryDescriptor,
    Workspace,
    WorkspaceModelBinding,
    WorkspaceRepositoryBinding,
)
from lode.infrastructure.git_source import (
    GitCredentialMaterial,
    GitRemoteRevisionResolver,
    GitRevisionResolver,
)


class InvestigationControlSnapshotStore:
    def __init__(
        self,
        session: AsyncSession,
        *,
        revision_resolver: GitRevisionResolver | None = None,
    ) -> None:
        self.session = session
        self.revision_resolver = revision_resolver or GitRemoteRevisionResolver(timeout_seconds=2.0)

    async def freeze(
        self,
        *,
        investigation_id: int,
        workspace_id: int,
        incident_source_revision: str | None,
    ) -> None:
        await self._freeze_repositories(investigation_id, workspace_id, incident_source_revision)
        await self._freeze_models(investigation_id, workspace_id)

    async def _freeze_repositories(
        self,
        investigation_id: int,
        workspace_id: int,
        incident_source_revision: str | None,
    ) -> None:
        rows = tuple(
            (
                await self.session.execute(
                    select(WorkspaceRepositoryBinding, GitRepository)
                    .join(
                        GitRepository,
                        GitRepository.id == WorkspaceRepositoryBinding.repository_id,
                    )
                    .where(
                        WorkspaceRepositoryBinding.workspace_id == workspace_id,
                        WorkspaceRepositoryBinding.state == "active",
                    )
                    .order_by(
                        WorkspaceRepositoryBinding.priority,
                        WorkspaceRepositoryBinding.id,
                    )
                )
            ).all()
        )
        credentials: dict[int, tuple[str | None, GitCredentialMaterial | None, bool]] = {}
        for _, repository in rows:
            credential_hash = None
            material = None
            credential_ready = repository.credential_id is None
            if repository.credential_id is not None:
                credential = await self.session.get(GitCredential, repository.credential_id)
                if credential is not None:
                    credential_hash = hashlib.sha256(
                        credential.secret_ciphertext.encode()
                    ).hexdigest()
                    try:
                        secret = decrypt_secret(credential.secret_ciphertext)
                    except CryptoError:
                        secret = None
                    if credential.readonly and secret:
                        material = GitCredentialMaterial(
                            credential.auth_type, credential.username, secret
                        )
                        credential_ready = True
            credentials[repository.id] = (
                credential_hash,
                material,
                credential_ready,
            )

        exact_results = await asyncio.gather(
            *(
                self._resolve_exact(
                    repository, credentials[repository.id], incident_source_revision
                )
                for binding, repository in rows
                if binding.role == "runtime_source" and incident_source_revision is not None
            )
        )
        runtime_rows = [
            (binding, repository)
            for binding, repository in rows
            if binding.role == "runtime_source" and incident_source_revision is not None
        ]
        exact_binding_ids = {
            binding.id
            for (binding, _), resolved in zip(runtime_rows, exact_results, strict=True)
            if resolved == incident_source_revision
        }
        branch_rows = [
            (binding, repository)
            for binding, repository in rows
            if binding.id not in exact_binding_ids
        ]
        branch_results = await asyncio.gather(
            *(
                self._resolve_branch(repository, credentials[repository.id])
                for _, repository in branch_rows
            )
        )
        branch_by_binding = {
            binding.id: resolved
            for (binding, _), resolved in zip(branch_rows, branch_results, strict=True)
        }

        for binding, repository in rows:
            credential_hash, _, _ = credentials[repository.id]
            if binding.id in exact_binding_ids:
                candidate_sha = incident_source_revision
                revision_role = "incident_source"
                resolution_status = "exact" if len(exact_binding_ids) == 1 else "unverified"
            else:
                candidate_sha = branch_by_binding.get(binding.id)
                revision_role = "repository_search_candidate"
                resolution_status = "unverified" if candidate_sha else "unresolved"
            payload = {
                "repository_binding_id": binding.id,
                "repository_id": repository.id,
                "credential_id": repository.credential_id,
                "binding_revision": binding.revision,
                "role": binding.role,
                "priority": binding.priority,
                "repo_url": repository.repo_url,
                "default_branch": repository.default_branch,
                "frozen_candidate_sha": candidate_sha,
                "frozen_revision_role": revision_role,
                "frozen_resolution_status": resolution_status,
                "repository_identity_hash": canonical_hash(
                    {
                        "repository_id": repository.id,
                        "repo_url": repository.repo_url,
                        "default_branch": repository.default_branch,
                        "scope": repository.scope,
                        "workspace_id": repository.workspace_id,
                    }
                ),
                "credential_identity_hash": credential_hash,
            }
            snapshot = InvestigationRepositorySnapshot(
                investigation_id=investigation_id,
                snapshot_hash=canonical_hash(payload),
                **payload,
            )
            self.session.add(snapshot)
            await self.session.flush()
            descriptor = (
                await self.session.execute(
                    select(RepositoryDescriptor)
                    .where(RepositoryDescriptor.repository_binding_id == binding.id)
                    .order_by(RepositoryDescriptor.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if descriptor is not None:
                content = {
                    "repository_snapshot_id": snapshot.id,
                    "descriptor": descriptor.descriptor,
                }
                self.session.add(
                    InvestigationDescriptorSnapshot(
                        investigation_id=investigation_id,
                        descriptor_kind="repository",
                        descriptor_id=descriptor.id,
                        descriptor_revision=descriptor.revision,
                        content=content,
                        evidence_refs=list(descriptor.evidence_refs),
                        snapshot_hash=canonical_hash(content),
                    )
                )

    async def _resolve_exact(
        self,
        repository: GitRepository,
        credential: tuple[str | None, GitCredentialMaterial | None, bool],
        revision: str,
    ) -> str | None:
        if not credential[2]:
            return None
        try:
            return await self.revision_resolver.resolve_revision(
                repo_url=repository.repo_url,
                revision=revision,
                credential=credential[1],
            )
        except (OSError, RuntimeError, ValueError):
            return None

    async def _resolve_branch(
        self,
        repository: GitRepository,
        credential: tuple[str | None, GitCredentialMaterial | None, bool],
    ) -> str | None:
        if not credential[2]:
            return None
        try:
            return await self.revision_resolver.resolve_branch(
                repo_url=repository.repo_url,
                branch=repository.default_branch,
                credential=credential[1],
            )
        except (OSError, RuntimeError, ValueError):
            return None

    async def _freeze_models(self, investigation_id: int, workspace_id: int) -> None:
        workspace = await self.session.get(Workspace, workspace_id)
        if workspace is None or workspace.model_policy_revision_id is None:
            return
        policy = await self.session.get(ModelPolicyRevision, workspace.model_policy_revision_id)
        if policy is None or policy.workspace_id != workspace_id:
            raise ValueError("Workspace model policy ownership is invalid")
        context_policy = await self.session.get(
            ContextPolicyRevision, policy.context_policy_revision_id
        )
        if context_policy is None or context_policy.workspace_id != workspace_id:
            raise ValueError("Workspace context policy ownership is invalid")
        policy_payload = {
            "role_policies": policy.role_policies,
            "verifier_policy": policy.verifier_policy,
            "eligible_bindings": policy.eligible_bindings,
        }
        context_payload = {
            "pinned_evidence_kinds": context_policy.pinned_evidence_kinds,
            "compression_levels": context_policy.compression_levels,
            "minimum_output_tokens": context_policy.minimum_output_tokens,
            "provider_safety_margin_tokens": context_policy.provider_safety_margin_tokens,
        }
        self.session.add(
            InvestigationModelPolicySnapshot(
                investigation_id=investigation_id,
                model_policy_revision_id=policy.id,
                context_policy_revision_id=context_policy.id,
                model_policy_revision=policy.revision,
                context_policy_revision=context_policy.revision,
                policy=policy_payload,
                context_policy=context_payload,
                snapshot_hash=canonical_hash(
                    {"policy": policy_payload, "context_policy": context_payload}
                ),
            )
        )
        eligible_bindings = _eligible_bindings(policy.eligible_bindings)
        explicit_ids = _binding_ids(policy.role_policies)
        if explicit_ids and not explicit_ids.issubset(eligible_bindings):
            raise ValueError("role policy references a non-eligible model binding")
        statement = select(WorkspaceModelBinding).where(
            WorkspaceModelBinding.workspace_id == workspace_id,
            WorkspaceModelBinding.state == "active",
            WorkspaceModelBinding.id.in_(eligible_bindings),
        )
        if explicit_ids:
            statement = statement.where(WorkspaceModelBinding.id.in_(explicit_ids))
        bindings = tuple(
            (
                await self.session.execute(
                    statement.order_by(
                        WorkspaceModelBinding.priority,
                        WorkspaceModelBinding.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(bindings) != len(eligible_bindings):
            raise ValueError("eligible model binding is missing or inactive")
        for binding in bindings:
            if binding.revision != eligible_bindings[binding.id]:
                raise ValueError("eligible model binding revision changed")
            deployment = await self.session.get(ModelDeployment, binding.model_deployment_id)
            if deployment is None:
                raise ValueError("model binding deployment is missing")
            provider = await self.session.get(AIProviderAccount, deployment.provider_account_id)
            if provider is None:
                raise ValueError("model deployment provider is missing")
            routing_policy = {
                "priority": binding.priority,
                "max_calls": binding.max_calls,
                "max_input_tokens": min(binding.max_input_tokens, deployment.max_input_tokens),
                "max_output_tokens": min(binding.max_output_tokens, deployment.max_output_tokens),
                "max_cost_per_call": float(binding.max_cost_per_call),
                "timeout_ms": binding.timeout_ms,
                "allowed_data_classes": list(binding.allowed_data_classes),
                "max_context_utilization": float(binding.max_context_utilization),
                "tokenizer_id": deployment.tokenizer_id,
                "model_capabilities": deployment.capabilities,
                "model_health": deployment.availability_state,
                "provider_health": provider.verification_status,
                "provider_kind": provider.provider_kind,
                "provider_base_url": provider.base_url,
                "provider_model_id": deployment.provider_model_id,
                "provider_data_residency": provider.data_residency,
                "provider_retention_mode": provider.retention_mode,
                "credential_identity_hash": hashlib.sha256(
                    provider.credential_ciphertext.encode()
                ).hexdigest(),
            }
            payload = {
                "workspace_model_binding_id": binding.id,
                "model_deployment_id": deployment.id,
                "provider_account_id": provider.id,
                "binding_revision": binding.revision,
                "model_deployment_revision": deployment.revision,
                "provider_account_revision": provider.revision,
                "execution_classes": binding.execution_classes,
                "allowed_roles": binding.allowed_roles,
                "routing_policy": routing_policy,
            }
            self.session.add(
                InvestigationModelBindingSnapshot(
                    investigation_id=investigation_id,
                    snapshot_hash=canonical_hash(payload),
                    **payload,
                )
            )


def _binding_ids(value: Any) -> frozenset[int]:
    values: set[int] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"binding_id", "workspace_model_binding_id"} and isinstance(child, int):
                values.add(child)
            else:
                values.update(_binding_ids(child))
    elif isinstance(value, list | tuple):
        for child in value:
            values.update(_binding_ids(child))
    return frozenset(values)


def _eligible_bindings(value: Any) -> dict[int, int]:
    if not isinstance(value, list) or not value:
        raise ValueError("model policy must contain eligible binding revisions")
    bindings: dict[int, int] = {}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"binding_id", "revision"}:
            raise ValueError("eligible binding revision is invalid")
        binding_id = item["binding_id"]
        revision = item["revision"]
        if (
            not isinstance(binding_id, int)
            or isinstance(binding_id, bool)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or min(binding_id, revision) < 1
            or binding_id in bindings
        ):
            raise ValueError("eligible binding revision is invalid")
        bindings[binding_id] = revision
    return bindings
