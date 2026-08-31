"""Audited semantic supplement for deterministic repository scans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.application.resource_analysis import (
    ResourceAnalysisPayload,
    resource_analysis_json_schema,
)
from lode.db.models import (
    AIProviderAccount,
    ModelPolicyRevision,
    ProviderAccountModel,
    RepositoryAnalysisModelInvocation,
    Workspace,
    WorkspaceModelBinding,
)
from lode.engine.llm import ModelConfig, ResponseSchema
from lode.infrastructure.model_runtime import DefaultModelGateway, ModelGateway
from lode.masking import mask_structure
from lode.model_catalog import find_model
from lode.resource_understanding.store import BoundRepositoryScan
from lode.resource_understanding.types import SemanticAnnotationDraft

_PROMPT_REVISION = "resource-analyst.v1"
_SCHEMA_REVISION = "resource-analysis.v1"
_SYSTEM_PROMPT = """Return only the required resource-analysis.v1 document.
Infer component identity and operational semantics only from the supplied deterministic
repository observations. Every component must cite existing build_unit_keys and
observation_refs. Do not invent resources, credentials, runtime state, or unanchored facts.
Entrypoints, dependencies, runbooks, and owners are semantic hints, never authorization."""


@dataclass(frozen=True, slots=True)
class ResourceAnalystResult:
    annotations: tuple[SemanticAnnotationDraft, ...]
    invocation_id: int
    status: str
    error_code: str | None


class PostgresResourceAnalyst:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway or DefaultModelGateway()

    async def analyze(
        self,
        *,
        job_id: int,
        workspace_id: int,
        scans: tuple[BoundRepositoryScan, ...],
    ) -> ResourceAnalystResult:
        request, _ = mask_structure(_request_payload(scans))
        request_text = json.dumps(
            request, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        request_hash = hashlib.sha256(request_text.encode()).hexdigest()
        async with self.session_factory() as session:
            route = await _select_route(session, workspace_id)
            if route is None:
                row = RepositoryAnalysisModelInvocation(
                    repository_analysis_job_id=job_id,
                    prompt_revision=_PROMPT_REVISION,
                    schema_revision=_SCHEMA_REVISION,
                    request_hash=request_hash,
                    status="unavailable",
                    error_code="resource_analyst_not_configured",
                    error_detail={"reason": "No healthy source-code model binding allows the role."},
                    latency_ms=0,
                )
                session.add(row)
                await session.commit()
                return ResourceAnalystResult((), row.id, row.status, row.error_code)
            binding, model, provider = route
            profile = find_model(
                provider.provider_kind,
                provider.protocol_id,
                model.provider_model_id,
            )
            if profile is None or profile.profile_hash != model.catalog_profile_hash:
                row = RepositoryAnalysisModelInvocation(
                    repository_analysis_job_id=job_id,
                    provider_account_id=provider.id,
                    provider_account_model_id=model.id,
                    workspace_model_binding_id=binding.id,
                    provider_account_revision=provider.revision,
                    provider_account_model_revision=model.revision,
                    binding_revision=binding.revision,
                    prompt_revision=_PROMPT_REVISION,
                    schema_revision=_SCHEMA_REVISION,
                    request_hash=request_hash,
                    status="failed",
                    error_code="resource_analyst_catalog_mismatch",
                    error_detail={"reason": "The selected model catalog profile is not current."},
                    latency_ms=0,
                )
                session.add(row)
                await session.commit()
                return ResourceAnalystResult((), row.id, row.status, row.error_code)
            config = ModelConfig(
                protocol_id=provider.protocol_id,
                base_url=provider.base_url,
                api_key_ciphertext=provider.api_key_ciphertext,
                model=model.provider_model_id,
                max_completion_tokens=min(profile.max_output_tokens, 16_000),
            )
            route_ids = (binding.id, model.id, provider.id)
            revisions = (binding.revision, model.revision, provider.revision)
            timeout_seconds = float(binding.timeout_ms) / 1_000

        try:
            completion = await self.gateway.complete(
                _SYSTEM_PROMPT,
                request_text,
                config,
                response_schema=ResponseSchema(
                    name="resource_analysis",
                    schema=resource_analysis_json_schema(),
                ),
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - model boundaries are audited and degraded
            completion = None
            failure = f"provider_{type(exc).__name__.lower()}"
        else:
            failure = completion.error_code

        payload: ResourceAnalysisPayload | None = None
        raw_payload: dict[str, Any] | None = None
        if completion is not None and completion.text is not None:
            try:
                decoded = json.loads(completion.text)
                if not isinstance(decoded, dict):
                    raise TypeError("resource analyst output must be an object")
                raw_payload = decoded
                payload = ResourceAnalysisPayload.model_validate(decoded)
                _validate_anchors(payload, scans)
            except (json.JSONDecodeError, TypeError, ValueError):
                failure = "invalid_resource_analysis"
        annotations = _annotations(payload) if payload is not None else ()
        masked_output, _ = mask_structure(raw_payload) if raw_payload is not None else (None, ())
        status = "succeeded" if payload is not None else "failed"
        response_hash = (
            hashlib.sha256(completion.text.encode()).hexdigest()
            if completion is not None and completion.text is not None
            else None
        )
        binding_id, model_id, provider_id = route_ids
        binding_revision, model_revision, provider_revision = revisions
        async with self.session_factory() as session:
            row = RepositoryAnalysisModelInvocation(
                repository_analysis_job_id=job_id,
                provider_account_id=provider_id,
                provider_account_model_id=model_id,
                workspace_model_binding_id=binding_id,
                provider_account_revision=provider_revision,
                provider_account_model_revision=model_revision,
                binding_revision=binding_revision,
                prompt_revision=_PROMPT_REVISION,
                schema_revision=_SCHEMA_REVISION,
                request_hash=request_hash,
                response_hash=response_hash,
                status=status,
                output_masked=masked_output,
                error_code=failure,
                error_detail=None if failure is None else {"reason": "Semantic analysis was not accepted."},
                input_tokens=None if completion is None else completion.input_tokens,
                output_tokens=None if completion is None else completion.output_tokens,
                latency_ms=0 if completion is None else completion.latency_ms,
                cost=Decimal(0),
            )
            session.add(row)
            await session.commit()
            return ResourceAnalystResult(annotations, row.id, row.status, row.error_code)


async def _select_route(session: AsyncSession, workspace_id: int):
    workspace = await session.get(Workspace, workspace_id)
    policy = (
        await session.get(ModelPolicyRevision, workspace.model_policy_revision_id)
        if workspace is not None and workspace.model_policy_revision_id is not None
        else None
    )
    if policy is None:
        return None
    expected_revisions = {
        int(item["binding_id"]): int(item["revision"])
        for item in policy.eligible_bindings
        if isinstance(item, dict)
        and isinstance(item.get("binding_id"), int)
        and isinstance(item.get("revision"), int)
    }
    if not expected_revisions:
        return None
    rows = (
        await session.execute(
            select(WorkspaceModelBinding, ProviderAccountModel, AIProviderAccount)
            .join(
                ProviderAccountModel,
                ProviderAccountModel.id == WorkspaceModelBinding.provider_account_model_id,
            )
            .join(
                AIProviderAccount,
                AIProviderAccount.id == ProviderAccountModel.provider_account_id,
            )
            .where(
                WorkspaceModelBinding.workspace_id == workspace_id,
                WorkspaceModelBinding.id.in_(expected_revisions),
                WorkspaceModelBinding.state == "active",
                WorkspaceModelBinding.allowed_roles.contains(["resource_analyst"]),
                WorkspaceModelBinding.allowed_data_classes.contains(["source_code"]),
                ProviderAccountModel.state == "active",
                ProviderAccountModel.availability_state == "healthy",
                AIProviderAccount.state == "active",
                AIProviderAccount.verification_status == "healthy",
            )
            .order_by(WorkspaceModelBinding.priority, WorkspaceModelBinding.id)
        )
    ).all()
    return next(
        (
            (binding, model, provider)
            for binding, model, provider in rows
            if binding.revision == expected_revisions.get(binding.id)
        ),
        None,
    )


def _request_payload(scans: tuple[BoundRepositoryScan, ...]) -> dict[str, Any]:
    observations = [item for scan in scans for item in scan.scan.observations]
    units = [item for scan in scans for item in scan.scan.build_units]
    return {
        "schema_version": _SCHEMA_REVISION,
        "observations": [
            {
                "source_ref": item.source_ref,
                "kind": item.observation_kind,
                "path": item.path,
                "facts": dict(item.structured_payload),
            }
            for item in observations[:500]
        ],
        "build_units": [
            {
                "candidate_key": item.candidate_key,
                "source_root": item.source_root,
                "build_system": item.build_system,
                "entrypoints": list(item.entrypoints),
                "observation_refs": list(item.observation_refs),
            }
            for item in units[:200]
        ],
    }


def _validate_anchors(
    payload: ResourceAnalysisPayload,
    scans: tuple[BoundRepositoryScan, ...],
) -> None:
    observations = {item.source_ref for scan in scans for item in scan.scan.observations}
    units = {item.candidate_key for scan in scans for item in scan.scan.build_units}
    for component in payload.components:
        if not set(component.observation_refs) <= observations:
            raise ValueError("resource analyst cited an unknown observation")
        if not set(component.build_unit_keys) <= units:
            raise ValueError("resource analyst cited an unknown build unit")


def _annotations(
    payload: ResourceAnalysisPayload | None,
) -> tuple[SemanticAnnotationDraft, ...]:
    if payload is None:
        return ()
    return tuple(
        SemanticAnnotationDraft(
            annotation_kind="component_identity",
            stable_key=(
                "component:ai-"
                + hashlib.sha256(
                    json.dumps(
                        sorted(item.build_unit_keys), separators=(",", ":")
                    ).encode()
                ).hexdigest()[:20]
            ),
            display_name=item.display_name,
            component_kind=item.component_kind,
            build_unit_keys=item.build_unit_keys,
            observation_refs=item.observation_refs,
            aliases=item.aliases,
            description=item.description,
            extra={
                "entrypoints": list(item.entrypoints),
                "dependencies": list(item.dependencies),
                "runbooks": list(item.runbooks),
                "owners": list(item.owners),
            },
        )
        for item in payload.components
    )
