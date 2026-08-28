"""Exercise safe discovery, graph publication, recovery, and intake snapshots."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from current_git_fixture import (
    FIXTURE_ADAPTER_ID,
    FIXTURE_ENDPOINT_HASH,
    ensure_repository_access,
)
from sqlalchemy import func, select

from lode.application.intake import ManualIncidentRequest, normalize_manual
from lode.config import settings
from lode.db.models import (
    BuildUnit,
    Component,
    ComponentSourceBinding,
    EvidenceAccessScope,
    GitRepository,
    IdentityResolution,
    InvestigationResourceGraphSnapshot,
    ResourceGraphRevision,
    ResourceGraphRevisionMember,
    User,
    Workspace,
    WorkspaceArchitectureContextRevision,
    WorkspacePermission,
    WorkspaceRepositoryBinding,
)
from lode.db.session import AsyncSessionLocal, engine
from lode.development.isolated_database import require_isolated_database
from lode.infrastructure.intake_store import PostgresIntakeStore
from lode.resource_understanding import (
    ManifestScanner,
    SemanticAnnotationDraft,
    repository_candidate_namespace,
)
from lode.resource_understanding.store import BoundRepositoryScan, ResourceGraphStore
from lode.security import create_token

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "resource_scanner"


async def _workspace(session) -> Workspace:
    workspace = (await session.execute(
        select(Workspace).where(Workspace.ingestion_topic == "resource-graph-check")
    )).scalar_one_or_none()
    if workspace is None:
        workspace = Workspace(name="Resource graph check", ingestion_topic="resource-graph-check")
        session.add(workspace)
        await session.flush()
        architecture_context = WorkspaceArchitectureContextRevision(
            workspace_id=workspace.id,
            entries=[],
            revision=1,
        )
        session.add(architecture_context)
        await session.flush()
        workspace.architecture_context_revision_id = architecture_context.id
    return workspace


async def _binding(session, workspace: Workspace, suffix: str, role: str) -> WorkspaceRepositoryBinding:
    url = f"https://example.invalid/resource-check-{suffix}.git"
    repository = (await session.execute(
        select(GitRepository).where(GitRepository.repo_url == url)
    )).scalar_one_or_none()
    if repository is None:
        repository = GitRepository(
            adapter_id=FIXTURE_ADAPTER_ID,
            endpoint_identity_hash=FIXTURE_ENDPOINT_HASH,
            external_repository_id=suffix,
            name=f"resource-check-{suffix}",
            full_name=f"fixtures/resource-check-{suffix}",
            repo_url=url,
            web_url=url.removesuffix(".git"),
            visibility="private",
        )
        session.add(repository)
        await session.flush()
    account_connection_id = await ensure_repository_access(session, workspace.id, repository)
    binding = (await session.execute(
        select(WorkspaceRepositoryBinding).where(
            WorkspaceRepositoryBinding.workspace_id == workspace.id,
            WorkspaceRepositoryBinding.repository_id == repository.id,
            WorkspaceRepositoryBinding.state == "active",
        )
    )).scalar_one_or_none()
    if binding is None:
        binding = WorkspaceRepositoryBinding(
            workspace_id=workspace.id,
            repository_id=repository.id,
            account_connection_id=account_connection_id,
            role=role,
        )
        session.add(binding)
        await session.flush()
    return binding


async def main() -> None:
    require_isolated_database("resource graph check")
    scanner = ManifestScanner()
    async with AsyncSessionLocal() as session:
        workspace = await _workspace(session)
        source = await _binding(session, workspace, "source", "runtime_source")
        worker = await _binding(session, workspace, "worker", "runtime_source")
        docs = await _binding(session, workspace, "docs", "documentation")
        conflict = await _binding(session, workspace, "conflict", "runtime_source")
        user = (await session.execute(
            select(User).where(User.username == "resource-check")
        )).scalar_one_or_none()
        if user is None:
            user = User(
                username="resource-check",
                display_name="Resource Check",
                password_hash="checker",
                status="active",
            )
            session.add(user)
            await session.flush()
        permission = await session.scalar(
            select(WorkspacePermission).where(
                WorkspacePermission.workspace_id == workspace.id,
                WorkspacePermission.user_id == user.id,
            )
        )
        if permission is None:
            session.add(
                WorkspacePermission(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    permission="viewer",
                )
            )
        await session.commit()

        source_scan = scanner.scan(
            FIXTURES / "single", "1" * 40,
            candidate_namespace=repository_candidate_namespace(source.id),
        )
        worker_scan = scanner.scan(
            FIXTURES / "python", "2" * 40,
            candidate_namespace=repository_candidate_namespace(worker.id),
        )
        docs_scan = scanner.scan(
            FIXTURES / "jvm", "3" * 40,
            candidate_namespace=repository_candidate_namespace(docs.id),
        )
        conflict_scan = scanner.scan(
            FIXTURES / "jvm", "4" * 40,
            candidate_namespace=repository_candidate_namespace(conflict.id),
        )
        source_root = next(item for item in source_scan.build_units if item.source_root == ".")
        worker_root = next(item for item in worker_scan.build_units if item.source_root == ".")
        annotation = SemanticAnnotationDraft(
            annotation_kind="component_identity",
            stable_key="component:resource-check",
            display_name="Resource Check",
            component_kind="service",
            build_unit_keys=(source_root.candidate_key, worker_root.candidate_key),
            observation_refs=tuple(
                item.source_ref
                for scan in (source_scan, worker_scan)
                for item in scan.observations
            ),
            aliases=("resource-check",),
        )
        bound_scans = (
            BoundRepositoryScan(source.id, source.repository_id, source_scan),
            BoundRepositoryScan(worker.id, worker.repository_id, worker_scan),
            BoundRepositoryScan(docs.id, docs.repository_id, docs_scan),
        )
        first = await ResourceGraphStore(session).publish(
            workspace_id=workspace.id,
            scans=bound_scans,
            annotations=(annotation,),
        )
        first_member_count = (await session.execute(
            select(func.count()).select_from(ResourceGraphRevisionMember).where(
                ResourceGraphRevisionMember.resource_graph_revision_id == first.revision_id
            )
        )).scalar_one()

        recovered = await ResourceGraphStore(session).publish(
            workspace_id=workspace.id,
            scans=tuple(reversed(bound_scans)),
            annotations=(annotation,),
        )
        assert recovered.reused is True
        assert recovered.revision_id == first.revision_id

        documentation_units = (await session.execute(
            select(func.count()).select_from(BuildUnit).where(
                BuildUnit.repository_binding_id == docs.id
            )
        )).scalar_one()
        component = (await session.execute(
            select(Component).where(
                Component.workspace_id == workspace.id,
                Component.stable_key == annotation.stable_key,
            )
        )).scalar_one()
        source_binding_count = (await session.execute(
            select(func.count()).select_from(ComponentSourceBinding).where(
                ComponentSourceBinding.component_id == component.id
            )
        )).scalar_one()
        assert documentation_units == 0
        assert component.identity_status == "verified"
        assert source_binding_count == 2

        request = ManualIncidentRequest.model_validate({
            "workspace_id": workspace.id,
            "occurred_at": "2026-08-26T12:00:00Z",
            "severity": "WARNING",
            "event": "resource.graph.snapshot",
            "trace_id": "resource-check-trace",
            "source_revision": "1" * 40,
            "error": {"type": "Check", "message": "snapshot", "stack": "frame", "cause": None},
        })
        intake = await PostgresIntakeStore(session).persist_manual(
            workspace_id=workspace.id,
            incident=normalize_manual(request),
            created_by=user.id,
        )
        snapshot = await session.get(InvestigationResourceGraphSnapshot, intake.investigation_id)
        assert snapshot is not None
        assert snapshot.resource_graph_revision_id == first.revision_id
        assert snapshot.graph_revision == first.revision

        conflict_root = next(item for item in conflict_scan.build_units if item.source_root == ".")
        conflict_annotation = SemanticAnnotationDraft(
            annotation_kind="component_identity",
            stable_key="component:resource-check-conflict",
            display_name="Resource Check Conflict",
            component_kind="service",
            build_unit_keys=(conflict_root.candidate_key,),
            observation_refs=conflict_root.observation_refs,
            aliases=("resource-check",),
        )
        conflict_graph = await ResourceGraphStore(session).publish(
            workspace_id=workspace.id,
            scans=(BoundRepositoryScan(
                conflict.id, conflict.repository_id, conflict_scan
            ),),
            annotations=(conflict_annotation,),
        )
        conflict_component = (await session.execute(
            select(Component).where(
                Component.workspace_id == workspace.id,
                Component.stable_key == conflict_annotation.stable_key,
            )
        )).scalar_one()
        assert conflict_component.identity_status == "ambiguous"

        changed_source_scan = scanner.scan(
            FIXTURES / "pnpm", "5" * 40,
            candidate_namespace=repository_candidate_namespace(source.id),
        )
        current_graph = await ResourceGraphStore(session).publish(
            workspace_id=workspace.id,
            scans=(BoundRepositoryScan(
                source.id, source.repository_id, changed_source_scan
            ),),
        )
        await session.refresh(component)
        assert component.state == "disabled"
        assert current_graph.revision == conflict_graph.revision + 1

        current_member_ids = set((await session.execute(
            select(ResourceGraphRevisionMember.identity_resolution_id).where(
                ResourceGraphRevisionMember.resource_graph_revision_id == current_graph.revision_id
            )
        )).scalars())
        original_member_ids = set((await session.execute(
            select(ResourceGraphRevisionMember.identity_resolution_id).where(
                ResourceGraphRevisionMember.resource_graph_revision_id == first.revision_id
            )
        )).scalars())
        invalidated_ids = original_member_ids - current_member_ids
        invalidated = [] if not invalidated_ids else (await session.execute(
            select(IdentityResolution).where(IdentityResolution.id.in_(invalidated_ids))
        )).scalars().all()
        assert invalidated
        assert all(row.valid_until is not None and row.invalidation_reason for row in invalidated)
        await session.refresh(snapshot)
        assert snapshot.resource_graph_revision_id == first.revision_id

        from lode.api.main import app

        token = create_token(user.id, settings.jwt_signing_key)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://resource.test"
        ) as client:
            response = await client.get(
                f"/workspaces/{workspace.id}/resource-graph-revisions/{current_graph.revision_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json()["revision"] == current_graph.revision

        graph_count = (await session.execute(
            select(func.count()).select_from(ResourceGraphRevision).where(
                ResourceGraphRevision.workspace_id == workspace.id
            )
        )).scalar_one()
        repeated_member_count = (await session.execute(
            select(func.count()).select_from(ResourceGraphRevisionMember).where(
                ResourceGraphRevisionMember.resource_graph_revision_id == first.revision_id
            )
        )).scalar_one()
        assert first_member_count == repeated_member_count
        assert (await session.execute(
            select(func.count()).select_from(WorkspaceRepositoryBinding).where(
                WorkspaceRepositoryBinding.workspace_id == workspace.id
            )
        )).scalar_one() == 4
        assert (await session.execute(
            select(func.count()).select_from(EvidenceAccessScope)
        )).scalar_one() == 0

        print(json.dumps({
            "build_units": (await session.execute(
                select(func.count()).select_from(BuildUnit).where(BuildUnit.workspace_id == workspace.id)
            )).scalar_one(),
            "component_identity_status": component.identity_status,
            "component_state_after_invalidation": component.state,
            "component_source_bindings": source_binding_count,
            "documentation_build_units": documentation_units,
            "first_graph_revision": first.revision,
            "current_graph_revision": current_graph.revision,
            "graph_revision_count": graph_count,
            "graph_reused_on_recovery": recovered.reused,
            "identity_conflict_status": conflict_component.identity_status,
            "invalidated_resolution_count": len(invalidated),
            "investigation_snapshot_revision": snapshot.graph_revision,
            "member_count": first_member_count,
        }, indent=2, sort_keys=True))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
