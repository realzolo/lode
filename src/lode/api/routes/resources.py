"""Read-only views over automatically derived Workspace resource knowledge."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.api.deps import assert_workspace_permission, require_user
from lode.db.models import (
    BuildUnit,
    Component,
    ComponentSourceBinding,
    IdentityResolution,
    ResourceGraphRevision,
    ResourceGraphRevisionMember,
    ResourceObservation,
    User,
)
from lode.db.session import AsyncSessionLocal


router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["resource-understanding"])
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


async def _authorized_session(workspace_id: int, user_id: int) -> AsyncSession:
    session = AsyncSessionLocal()
    user = await session.get(User, user_id)
    if user is None:
        await session.close()
        raise HTTPException(status_code=401, detail="user not found")
    try:
        await assert_workspace_permission(session, user, workspace_id, "viewer")
    except Exception:
        await session.close()
        raise
    return session


@router.get("/build-units")
async def list_build_units(
    workspace_id: int,
    limit: Limit = 100,
    offset: Offset = 0,
    user_id: int = Depends(require_user),
) -> dict:
    session = await _authorized_session(workspace_id, user_id)
    try:
        rows = (await session.execute(
            select(BuildUnit)
            .where(BuildUnit.workspace_id == workspace_id)
            .order_by(BuildUnit.stable_key)
            .limit(limit)
            .offset(offset)
        )).scalars().all()
        return {"items": [
            {
                "id": row.id,
                "repository_binding_id": row.repository_binding_id,
                "stable_key": row.stable_key,
                "source_root": row.source_root,
                "build_system": row.build_system,
                "manifest_paths": row.manifest_paths,
                "entrypoints": row.entrypoints,
                "artifact_hints": row.artifact_hints,
                "identity_status": row.identity_status,
                "state": row.state,
                "ownership_priority": row.ownership_priority,
                "revision": row.revision,
            }
            for row in rows
        ], "limit": limit, "offset": offset}
    finally:
        await session.close()


@router.get("/components")
async def list_components(
    workspace_id: int,
    limit: Limit = 100,
    offset: Offset = 0,
    user_id: int = Depends(require_user),
) -> dict:
    session = await _authorized_session(workspace_id, user_id)
    try:
        rows = (await session.execute(
            select(Component)
            .where(Component.workspace_id == workspace_id)
            .order_by(Component.stable_key)
            .limit(limit)
            .offset(offset)
        )).scalars().all()
        component_ids = [row.id for row in rows]
        binding_rows = [] if not component_ids else (await session.execute(
            select(ComponentSourceBinding, BuildUnit.stable_key)
            .join(BuildUnit, BuildUnit.id == ComponentSourceBinding.build_unit_id)
            .where(ComponentSourceBinding.component_id.in_(component_ids))
            .order_by(ComponentSourceBinding.component_id, BuildUnit.stable_key)
        )).all()
        by_component: dict[int, list[dict]] = {item: [] for item in component_ids}
        for binding, build_key in binding_rows:
            by_component[binding.component_id].append({
                "build_unit_id": binding.build_unit_id,
                "build_unit_key": build_key,
                "role": binding.role,
                "path_prefix": binding.path_prefix,
            })
        return {"items": [
            {
                "id": row.id,
                "stable_key": row.stable_key,
                "display_name": row.display_name,
                "kind": row.kind,
                "description": row.description,
                "identity_status": row.identity_status,
                "state": row.state,
                "discovery_basis": row.discovery_basis,
                "root_provenance_families": row.root_provenance_families,
                "revision": row.revision,
                "source_bindings": by_component[row.id],
            }
            for row in rows
        ], "limit": limit, "offset": offset}
    finally:
        await session.close()


@router.get("/resource-graph-revisions")
async def list_resource_graph_revisions(
    workspace_id: int,
    limit: Limit = 100,
    offset: Offset = 0,
    user_id: int = Depends(require_user),
) -> dict:
    session = await _authorized_session(workspace_id, user_id)
    try:
        rows = (await session.execute(
            select(ResourceGraphRevision)
            .where(ResourceGraphRevision.workspace_id == workspace_id)
            .order_by(ResourceGraphRevision.revision.desc())
            .limit(limit)
            .offset(offset)
        )).scalars().all()
        return {"items": [_graph_summary(row) for row in rows], "limit": limit, "offset": offset}
    finally:
        await session.close()


@router.get("/resource-graph-revisions/{revision_id}")
async def get_resource_graph_revision(
    workspace_id: int,
    revision_id: int,
    user_id: int = Depends(require_user),
) -> dict:
    session = await _authorized_session(workspace_id, user_id)
    try:
        graph = (await session.execute(
            select(ResourceGraphRevision).where(
                ResourceGraphRevision.id == revision_id,
                ResourceGraphRevision.workspace_id == workspace_id,
            )
        )).scalar_one_or_none()
        if graph is None:
            raise HTTPException(status_code=404, detail="resource graph revision not found")
        members = (await session.execute(
            select(IdentityResolution)
            .join(
                ResourceGraphRevisionMember,
                ResourceGraphRevisionMember.identity_resolution_id == IdentityResolution.id,
            )
            .where(ResourceGraphRevisionMember.resource_graph_revision_id == graph.id)
            .order_by(IdentityResolution.resolution_kind, IdentityResolution.stable_key)
        )).scalars().all()
        return {
            **_graph_summary(graph),
            "members": [_resolution_view(row) for row in members],
        }
    finally:
        await session.close()


@router.get("/resource-observations")
async def list_resource_observations(
    workspace_id: int,
    limit: Limit = 100,
    offset: Offset = 0,
    user_id: int = Depends(require_user),
) -> dict:
    session = await _authorized_session(workspace_id, user_id)
    try:
        rows = (await session.execute(
            select(ResourceObservation)
            .where(ResourceObservation.workspace_id == workspace_id)
            .order_by(ResourceObservation.id.desc())
            .limit(limit)
            .offset(offset)
        )).scalars().all()
        return {"items": [
            {
                "id": row.id,
                "source_kind": row.source_kind,
                "source_ref": row.source_ref,
                "observation_kind": row.observation_kind,
                "structured_payload": row.structured_payload,
                "content_hash": row.content_hash,
                "repository_id": row.repository_id,
                "source_revision": row.source_revision,
                "path": row.path,
                "root_provenance_id": row.root_provenance_id,
                "source_family": row.source_family,
                "trust_class": row.trust_class,
                "observed_at": row.observed_at,
                "parser_name": row.parser_name,
                "parser_version": row.parser_version,
            }
            for row in rows
        ], "limit": limit, "offset": offset}
    finally:
        await session.close()


@router.get("/identity-resolutions")
async def list_identity_resolutions(
    workspace_id: int,
    limit: Limit = 100,
    offset: Offset = 0,
    user_id: int = Depends(require_user),
) -> dict:
    session = await _authorized_session(workspace_id, user_id)
    try:
        rows = (await session.execute(
            select(IdentityResolution)
            .where(IdentityResolution.workspace_id == workspace_id)
            .order_by(IdentityResolution.id.desc())
            .limit(limit)
            .offset(offset)
        )).scalars().all()
        return {"items": [_resolution_view(row) for row in rows], "limit": limit, "offset": offset}
    finally:
        await session.close()


def _graph_summary(row: ResourceGraphRevision) -> dict:
    return {
        "id": row.id,
        "revision": row.revision,
        "parent_revision_id": row.parent_revision_id,
        "input_hash": row.input_hash,
        "validator_version": row.validator_version,
        "diff": row.diff,
        "published_at": row.published_at,
    }


def _resolution_view(row: IdentityResolution) -> dict:
    return {
        "id": row.id,
        "stable_key": row.stable_key,
        "resolution_kind": row.resolution_kind,
        "resolved_payload": row.resolved_payload,
        "status": row.status,
        "evidence_basis": row.evidence_basis,
        "observation_refs": row.observation_refs,
        "annotation_refs": row.annotation_refs,
        "root_provenance_refs": row.root_provenance_refs,
        "validator_version": row.validator_version,
        "valid_from": row.valid_from,
        "valid_until": row.valid_until,
        "invalidation_reason": row.invalidation_reason,
        "resolution_hash": row.resolution_hash,
    }
