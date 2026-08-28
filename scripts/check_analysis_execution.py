"""Exercise frozen multi-model routing, context isolation, replay, and drift failure."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from current_git_fixture import (
    FIXTURE_ADAPTER_ID,
    FIXTURE_ENDPOINT_HASH,
    ensure_repository_access,
)
from sqlalchemy import func, select

from lode.application.intake import ManualIncidentRequest, normalize_manual
from lode.crypto import encrypt_secret
from lode.db.models import (
    AIInvocation,
    AIProviderAccount,
    ContextBundleRevision,
    ContextPolicyRevision,
    ContextSummaryArtifact,
    EvidenceArtifact,
    GitAccount,
    GitAccountCredentialRevision,
    GitRepository,
    InvestigationModelBindingSnapshot,
    InvestigationRepositorySnapshot,
    ModelPolicyRevision,
    ModelRoutingDecision,
    ProviderAccountModel,
    User,
    Workspace,
    WorkspaceArchitectureContextRevision,
    WorkspaceModelBinding,
    WorkspaceRepositoryBinding,
)
from lode.db.session import AsyncSessionLocal, engine
from lode.development.isolated_database import require_isolated_database
from lode.domain.investigation import canonical_hash
from lode.domain.model_execution import ContextEvidence, ModelTask
from lode.domain.types import ModelRole
from lode.engine.llm import CompletionResult, ResponseSchema
from lode.infrastructure.git_source import GitSourceHit
from lode.infrastructure.intake_store import PostgresIntakeStore
from lode.infrastructure.model_runtime import (
    ModelRuntimeUnavailable,
    PostgresModelRuntime,
)
from lode.infrastructure.report_store import (
    PostgresReportStore,
    ReportValidationError,
)
from lode.infrastructure.source_store import PostgresSourceStore
from lode.model_catalog import require_model


class FixtureGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(
        self,
        system_prompt,
        user_prompt,
        config,
        *,
        response_schema,
        timeout_seconds,
    ) -> CompletionResult:
        self.calls.append((config.model, response_schema.name))
        if response_schema.name == "context_summary":
            request = json.loads(user_prompt)
            evidence_refs = [item["artifact_id"] for item in request.get("evidence", [])]
            counter_refs = request["state_packet"].get("required_counter_evidence_refs", [])
            output = {
                "summary": {"observed_status": 503},
                "input_evidence_refs": evidence_refs,
                "covered_claim_refs": evidence_refs[:1],
                "retained_counter_evidence_refs": counter_refs,
                "omitted_evidence_refs": [
                    value for value in evidence_refs if value not in counter_refs
                ],
            }
        else:
            output = {"result": response_schema.name}
        return CompletionResult(
            json.dumps(output),
            2,
            20,
            5,
            25,
            "provider",
            attempt_count=1,
        )


async def _provider(session, suffix: str, name: str) -> AIProviderAccount:
    provider = AIProviderAccount(
        name=f"{name}-{suffix}",
        provider_kind="openai",
        protocol_id="openai.responses.v1",
        base_url="https://models.example.invalid",
        api_key_ciphertext=encrypt_secret(f"{name}-secret") or "",
        state="active",
        verification_status="healthy",
        verified_at=datetime.now(UTC),
        revision=1,
    )
    session.add(provider)
    await session.flush()
    return provider


async def _deployment(session, provider: AIProviderAccount, name: str) -> ProviderAccountModel:
    profile = require_model("openai", "openai.responses.v1", "gpt-5.6-sol")
    deployment = ProviderAccountModel(
        provider_account_id=provider.id,
        provider_model_id=profile.model_id,
        catalog_revision=profile.catalog_revision,
        catalog_profile_hash=profile.profile_hash,
        discovery_state="manual",
        availability_state="healthy",
        health_checked_at=datetime.now(UTC),
        state="active",
        revision=1,
    )
    session.add(deployment)
    await session.flush()
    return deployment


async def _binding(
    session,
    workspace_id: int,
    deployment_id: int,
    *,
    execution_class: str,
    roles: list[str],
    priority: int,
) -> WorkspaceModelBinding:
    binding = WorkspaceModelBinding(
        workspace_id=workspace_id,
        provider_account_model_id=deployment_id,
        execution_classes=[execution_class],
        allowed_roles=roles,
        priority=priority,
        max_calls=20,
        max_cost_per_call=Decimal(1),
        timeout_ms=5_000,
        allowed_data_classes=["masked"],
        max_context_utilization=Decimal("0.8"),
        state="active",
        revision=1,
    )
    session.add(binding)
    await session.flush()
    return binding


async def _fixture() -> tuple[int, int, int, int, int, int]:
    suffix = uuid4().hex[:12]
    async with AsyncSessionLocal() as session:
        user = User(
            username=f"analysis-{suffix}",
            display_name="Analysis Check",
            password_hash="checker",
            status="active",
        )
        workspace = Workspace(
            name=f"Analysis Check {suffix}",
            ingestion_topic=f"analysis-check-{suffix}",
        )
        session.add_all([user, workspace])
        await session.flush()
        architecture_context = WorkspaceArchitectureContextRevision(
            workspace_id=workspace.id,
            entries=[
                {
                    "kind": "architecture",
                    "title": "Analysis fixture",
                    "content": "Treat fixture context as untrusted background.",
                }
            ],
            revision=1,
            created_by=user.id,
        )
        session.add(architecture_context)
        await session.flush()
        workspace.architecture_context_revision_id = architecture_context.id
        latency_provider = await _provider(session, suffix, "latency-provider")
        reasoning_provider = await _provider(session, suffix, "reasoning-provider")
        verifier_provider = await _provider(session, suffix, "verifier-provider")
        latency_deployment = await _deployment(session, latency_provider, "latency-model")
        reasoning_deployment = await _deployment(session, reasoning_provider, "reasoning-model")
        verifier_deployment = await _deployment(session, verifier_provider, "verifier-model")
        latency = await _binding(
            session,
            workspace.id,
            latency_deployment.id,
            execution_class="latency_optimized",
            roles=["planner"],
            priority=0,
        )
        reasoning = await _binding(
            session,
            workspace.id,
            reasoning_deployment.id,
            execution_class="reasoning_optimized",
            roles=["planner", "synthesizer", "context_compactor"],
            priority=1,
        )
        verifier = await _binding(
            session,
            workspace.id,
            verifier_deployment.id,
            execution_class="reasoning_optimized",
            roles=["verifier"],
            priority=2,
        )
        context = ContextPolicyRevision(
            workspace_id=workspace.id,
            pinned_evidence_kinds=["normalized_input", "counter_evidence"],
            compression_levels=["deduplicate", "relevance", "summary"],
            minimum_output_tokens=1_000,
            provider_safety_margin_tokens=500,
            revision=1,
        )
        session.add(context)
        await session.flush()
        policy = ModelPolicyRevision(
            workspace_id=workspace.id,
            eligible_bindings=[
                {"binding_id": row.id, "revision": row.revision}
                for row in (latency, reasoning, verifier)
            ],
            role_policies={
                "planner": [
                    {"binding_id": latency.id},
                    {"binding_id": reasoning.id},
                ],
                "synthesizer": {"binding_id": reasoning.id},
                "verifier": {"binding_id": verifier.id},
            },
            context_policy_revision_id=context.id,
            verifier_policy={
                "separate_account_model": True,
                "separate_provider": True,
            },
            revision=1,
        )
        session.add(policy)
        await session.flush()
        workspace.model_policy_revision_id = policy.id
        await session.commit()

        request = ManualIncidentRequest.model_validate(
            {
                "workspace_id": workspace.id,
                "occurred_at": "2026-08-27T10:00:00Z",
                "severity": "WARNING",
                "event": "analysis.execution.check",
                "trace_id": "analysis-trace",
                "source_revision": "e" * 40,
                "error": {
                    "type": "CheckError",
                    "message": "analysis execution",
                    "stack": "frame",
                    "cause": None,
                },
            }
        )
        intake = await PostgresIntakeStore(session).persist_manual(
            workspace_id=workspace.id,
            incident=normalize_manual(request),
            created_by=user.id,
        )
        assert intake.investigation_id is not None
        return (
            intake.investigation_id,
            workspace.id,
            latency_provider.id,
            latency_deployment.id,
            reasoning_deployment.id,
            verifier_deployment.id,
        )


def _task(role: ModelRole, **changes) -> ModelTask:
    values = {
        "role": role,
        "required_context_tokens": 1,
        "reserved_output_tokens": 1_000,
        "provider_safety_margin_tokens": 500,
        "data_class": "masked",
        "component_count": 1,
        "repository_count": 1,
        "contradiction_count": 0,
        "causal_depth": 1,
        "conclusion_risk": "low",
    }
    values.update(changes)
    return ModelTask(**values)


async def main() -> None:
    require_isolated_database("analysis execution check")
    (
        investigation_id,
        workspace_id,
        latency_provider_id,
        latency_deployment_id,
        reasoning_deployment_id,
        verifier_deployment_id,
    ) = await _fixture()
    gateway = FixtureGateway()
    runtime = PostgresModelRuntime(AsyncSessionLocal, gateway=gateway)
    schema = ResponseSchema(
        name="analysis_check",
        schema={"type": "object", "additionalProperties": True},
    )
    common = {
        "investigation_id": investigation_id,
        "evidence": (),
        "system_prompt": "Return the schema.",
        "response_schema": schema,
        "prompt_revision": "analysis-check.1",
        "schema_revision": "analysis-check.v1",
        "remaining_calls": 20,
        "remaining_cost": 20.0,
    }
    simple_state = {"objective": "classify", "hidden_reasoning": "must not persist"}
    simple = await runtime.invoke(
        **common,
        task=_task(ModelRole.PLANNER),
        state_packet=simple_state,
    )
    replay = await runtime.invoke(
        **common,
        task=_task(ModelRole.PLANNER),
        state_packet=simple_state,
    )
    assert simple.invocation_id == replay.invocation_id
    complex_result = await runtime.invoke(
        **common,
        task=_task(
            ModelRole.PLANNER,
            repository_count=2,
            contradiction_count=1,
            causal_depth=3,
            conclusion_risk="high",
        ),
        state_packet={"objective": "resolve contradiction"},
    )
    synthesis = await runtime.invoke(
        **common,
        task=_task(ModelRole.SYNTHESIZER, conclusion_risk="high"),
        state_packet={"objective": "synthesize"},
    )
    async with AsyncSessionLocal() as session:
        synthesis_row = await session.get(AIInvocation, synthesis.invocation_id)
        assert synthesis_row is not None
    verification = await runtime.invoke(
        **common,
        task=_task(
            ModelRole.VERIFIER,
            conclusion_risk="high",
            prior_synthesizer_account_model_id=synthesis_row.provider_account_model_id,
            prior_synthesizer_provider_id=synthesis_row.provider_account_id,
        ),
        state_packet={"objective": "verify", "raw_model_output": "must not persist"},
        verifier_separate_account_model=True,
        verifier_separate_provider=True,
    )
    assert len(gateway.calls) == 4

    async with AsyncSessionLocal() as session:
        large_artifacts: list[EvidenceArtifact] = []
        for index, evidence_class in enumerate(("runtime", "counter_evidence"), start=1):
            content = {"status": 503, "detail": f"segment-{index}-" + "x" * 18_000}
            artifact = EvidenceArtifact(
                investigation_id=investigation_id,
                collection_id=None,
                artifact_kind="log_event",
                evidence_class=evidence_class,
                content_masked=content,
                content_hash=canonical_hash(content),
                provenance={"fixture": "context-pressure"},
                source_revision=None,
                data_class="masked",
                prompt_injection_markers=[],
            )
            session.add(artifact)
            large_artifacts.append(artifact)
        await session.flush()
        pressure_evidence = tuple(
            ContextEvidence(
                artifact_id=artifact.id,
                artifact_kind=artifact.artifact_kind,
                content=artifact.content_masked,
                token_count=0,
                relevance=1,
                pinned=False,
                counter_evidence=artifact.evidence_class == "counter_evidence",
                data_class="masked",
            )
            for artifact in large_artifacts
        )
        await session.commit()
    compacted = await runtime.invoke(
        **{**common, "evidence": pressure_evidence},
        task=_task(ModelRole.PLANNER),
        state_packet={"objective": "classify a long evidence set"},
    )
    assert compacted.payload is not None
    assert len(gateway.calls) == 5

    async with AsyncSessionLocal() as session:
        latency_provider = await session.get(AIProviderAccount, latency_provider_id)
        assert latency_provider is not None
        latency_provider.api_key_ciphertext = encrypt_secret("rotated-secret") or ""
        latency_provider.revision += 1
        await session.commit()
    try:
        await runtime.invoke(
            **common,
            task=_task(ModelRole.PLANNER),
            state_packet={"objective": "new simple route after drift"},
        )
    except ModelRuntimeUnavailable as exc:
        assert exc.code == "model_capability_unavailable"
        unavailable_route_id = exc.routing_decision_id
    else:
        raise AssertionError("control-plane drift did not invalidate the frozen route")
    assert len(gateway.calls) == 5

    async with AsyncSessionLocal() as session:
        snapshots = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(InvestigationModelBindingSnapshot)
                    .where(InvestigationModelBindingSnapshot.investigation_id == investigation_id)
                )
            ).scalar_one()
        )
        routes = tuple(
            (
                await session.execute(
                    select(ModelRoutingDecision)
                    .where(ModelRoutingDecision.investigation_id == investigation_id)
                    .order_by(ModelRoutingDecision.id)
                )
            )
            .scalars()
            .all()
        )
        contexts = tuple(
            (
                await session.execute(
                    select(ContextBundleRevision)
                    .where(ContextBundleRevision.investigation_id == investigation_id)
                    .order_by(ContextBundleRevision.id)
                )
            )
            .scalars()
            .all()
        )
        invocations = tuple(
            (
                await session.execute(
                    select(AIInvocation)
                    .where(AIInvocation.investigation_id == investigation_id)
                    .order_by(AIInvocation.id)
                )
            )
            .scalars()
            .all()
        )
        summaries = tuple(
            (
                await session.execute(
                    select(ContextSummaryArtifact)
                    .where(ContextSummaryArtifact.investigation_id == investigation_id)
                    .order_by(ContextSummaryArtifact.id)
                )
            )
            .scalars()
            .all()
        )
    assert snapshots == 3
    assert len(routes) == 6 and routes[-1].id == unavailable_route_id
    assert routes[0].execution_class == "latency_optimized"
    assert routes[0].model_binding_snapshot_id is not None
    assert routes[1].execution_class == "reasoning_optimized"
    assert routes[-1].model_binding_snapshot_id is None
    assert routes[-1].allowed_input_tokens == routes[-1].allowed_output_tokens == 0
    assert len(contexts) == len(invocations) == 5
    assert not summaries
    by_id = {row.id: row for row in invocations}
    compacted_context = next(
        row
        for row in contexts
        if row.id == by_id[compacted.invocation_id].context_bundle_revision_id
    )
    assert compacted_context.summary_refs == []
    assert len(compacted_context.evidence_refs) == 2
    assert all("hidden_reasoning" not in row.state_packet for row in contexts)
    assert all("raw_model_output" not in row.state_packet for row in contexts)
    assert {row.role for row in contexts} == {
        "planner",
        "synthesizer",
        "verifier",
    }
    assert by_id[simple.invocation_id].provider_account_model_id == latency_deployment_id
    assert by_id[complex_result.invocation_id].provider_account_model_id == reasoning_deployment_id
    assert by_id[verification.invocation_id].provider_account_model_id == verifier_deployment_id
    report_result = await _check_report_publication(
        investigation_id=investigation_id,
        workspace_id=workspace_id,
        synthesizer_invocation_id=synthesis.invocation_id,
        verifier_invocation_id=verification.invocation_id,
    )
    print(
        json.dumps(
            {
                "snapshots": snapshots,
                "routing_decisions": len(routes),
                "context_bundles": len(contexts),
                "invocations": len(invocations),
                "provider_calls": len(gateway.calls),
                "replay_reused": simple.invocation_id == replay.invocation_id,
                "drift_failed_closed": routes[-1].model_binding_snapshot_id is None,
                "context_summaries": len(summaries),
                "report_result": report_result,
            },
            sort_keys=True,
        )
    )
    await engine.dispose()


async def _check_report_publication(
    *,
    investigation_id: int,
    workspace_id: int,
    synthesizer_invocation_id: int,
    verifier_invocation_id: int,
) -> str:
    revision = "e" * 40
    async with AsyncSessionLocal() as session:
        repository = GitRepository(
            adapter_id=FIXTURE_ADAPTER_ID,
            endpoint_identity_hash=FIXTURE_ENDPOINT_HASH,
            external_repository_id=str(investigation_id),
            name=f"analysis-source-{investigation_id}",
            full_name=f"fixtures/analysis-source-{investigation_id}",
            repo_url=f"file:///analysis-source-{investigation_id}",
            web_url=f"https://example.invalid/analysis-source-{investigation_id}",
            default_branch="main",
            visibility="private",
        )
        session.add(repository)
        await session.flush()
        account_connection_id = await ensure_repository_access(
            session, workspace_id, repository
        )
        account = (
            await session.execute(
                select(GitAccount).where(
                    GitAccount.adapter_id == FIXTURE_ADAPTER_ID,
                    GitAccount.endpoint_identity_hash == FIXTURE_ENDPOINT_HASH,
                    GitAccount.external_account_id == str(workspace_id),
                )
            )
        ).scalar_one()
        credential = await session.get(
            GitAccountCredentialRevision, account.current_credential_revision_id
        )
        assert credential is not None
        binding = WorkspaceRepositoryBinding(
            workspace_id=workspace_id,
            repository_id=repository.id,
            account_connection_id=account_connection_id,
            role="runtime_source",
            priority=0,
            state="active",
            revision=1,
        )
        session.add(binding)
        await session.flush()
        snapshot_payload = {
            "repository_binding_id": binding.id,
            "repository_id": repository.id,
            "account_connection_id": account.id,
            "credential_revision_id": credential.id,
            "binding_revision": 1,
            "role": "runtime_source",
            "priority": 0,
            "repo_url": repository.repo_url,
            "default_branch": "main",
            "frozen_candidate_sha": revision,
            "frozen_revision_role": "incident_source",
            "frozen_resolution_status": "exact",
            "repository_identity_hash": canonical_hash(
                {
                    "repository_id": repository.id,
                    "repo_url": repository.repo_url,
                    "default_branch": repository.default_branch,
                    "adapter_id": repository.adapter_id,
                    "endpoint_identity_hash": repository.endpoint_identity_hash,
                    "external_repository_id": repository.external_repository_id,
                }
            ),
            "credential_identity_hash": credential.credential_identity_hash,
        }
        snapshot = InvestigationRepositorySnapshot(
            investigation_id=investigation_id,
            snapshot_hash=canonical_hash(snapshot_payload),
            **snapshot_payload,
        )
        session.add(snapshot)
        await session.flush()
        archived = await PostgresSourceStore(session).archive(
            investigation_id=investigation_id,
            operation_id=None,
            repository_snapshot_id=snapshot.id,
            revision_role="incident_source",
            requested_ref=revision,
            resolved_sha=revision,
            hits=(
                GitSourceHit(
                    path="src/checkout.py",
                    symbol="checkout",
                    start_line=10,
                    end_line=14,
                    content="def checkout():\n    raise CheckoutTimeout()",
                    selection_reason="exact incident stack frame",
                ),
            ),
        )
        artifact_id = archived.artifact_ids[0]
        synthesis = _confirmed_report(
            repository_id=repository.id,
            source_artifact_id=artifact_id,
            source_assessment_id=archived.source_assessment_id,
            revision=revision,
        )
        broken = json.loads(json.dumps(synthesis))
        broken["code_findings"][0]["path"] = "src/wrong.py"
        store = PostgresReportStore(session)
        try:
            await store.publish(
                investigation_id=investigation_id,
                synthesis=broken,
                synthesizer_invocation_id=synthesizer_invocation_id,
                verification=_approved_verification(artifact_id),
                verifier_invocation_id=verifier_invocation_id,
            )
        except ReportValidationError as exc:
            assert str(exc) == "code_finding_source_provenance_mismatch"
        else:
            raise AssertionError("mismatched code provenance was accepted")
        published = await store.publish(
            investigation_id=investigation_id,
            synthesis=synthesis,
            synthesizer_invocation_id=synthesizer_invocation_id,
            verification=_approved_verification(artifact_id),
            verifier_invocation_id=verifier_invocation_id,
        )
        assert published.result_state == "confirmed"
        assert len(published.finding_ids) == 1
        replayed = await store.publish(
            investigation_id=investigation_id,
            synthesis=synthesis,
            synthesizer_invocation_id=synthesizer_invocation_id,
            verification=_approved_verification(artifact_id),
            verifier_invocation_id=verifier_invocation_id,
        )
        assert replayed.report_hash == published.report_hash
        assert replayed.finding_ids == published.finding_ids
        await session.commit()
        return published.result_state


def _confirmed_report(
    *,
    repository_id: int,
    source_artifact_id: int,
    source_assessment_id: int,
    revision: str,
) -> dict:
    return {
        "result_state": "confirmed",
        "headline": "Checkout fails on the timeout branch",
        "summary": "The exact incident revision raises instead of preserving the result.",
        "incident_cause": {
            "status": "confirmed",
            "mechanism": "application_code",
            "causal_chain": ["timeout", "faulty branch", "request failure"],
            "evidence_refs": [source_artifact_id],
        },
        "code_diagnosis": {
            "status": "confirmed",
            "summary": "The timeout branch raises the wrong error.",
            "finding_indices": [0],
        },
        "code_findings": [
            {
                "status": "confirmed",
                "source_artifact_id": source_artifact_id,
                "source_assessment_id": source_assessment_id,
                "repository_id": repository_id,
                "revision": revision,
                "revision_role": "incident_source",
                "path": "src/checkout.py",
                "symbol": "checkout",
                "start_line": 10,
                "end_line": 14,
                "issue_type": "error_preservation",
                "faulty_behavior": "Raises a replacement timeout error.",
                "why_wrong": "The original failure context is discarded.",
                "expected_behavior": "Preserve the original failure context.",
                "trigger_condition": "The checkout call reaches its timeout branch.",
                "propagation": ["checkout", "request"],
                "incident_evidence_refs": [source_artifact_id],
                "supporting_evidence_refs": [source_artifact_id],
                "counter_evidence_refs": [],
                "missing_validation": [],
                "test_scenario": "Force the checkout timeout branch.",
            }
        ],
        "participants": [],
        "timeline_summary": [],
        "source_assessments": [],
        "configuration_assessments": [],
        "confirmed_facts": [
            {
                "text": "The exact source revision contains the faulty branch.",
                "evidence_refs": [source_artifact_id],
            }
        ],
        "counter_evidence": [],
        "evidence_gaps": [],
        "next_step": "Add a regression test for the timeout branch.",
    }


def _approved_verification(artifact_id: int) -> dict:
    return {
        "verdict": "approved",
        "finding_verdicts": [
            {
                "finding_index": 0,
                "verdict": "approved",
                "reasons": ["The exact source anchor and branch are present."],
                "evidence_refs": [artifact_id],
            }
        ],
        "alternative_explanations_checked": ["configuration", "dependency"],
        "counter_evidence_refs": [],
        "reasons": ["The source and incident mechanism are consistent."],
    }


if __name__ == "__main__":
    asyncio.run(main())
