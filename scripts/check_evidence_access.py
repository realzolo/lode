"""Exercise durable native-read authorization, ValueRef binding, and replay defense."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from lode.application.intake import ManualIncidentRequest, normalize_manual
from lode.crypto import decrypt_value, encrypt_value
from lode.db.models import (
    AIInvocation,
    AIProviderAccount,
    AuthorizedEvidenceRead,
    ContextBundleRevision,
    ContextPolicyRevision,
    EvidenceAccessDecision,
    EvidenceAccessScope,
    EvidenceArtifact,
    EvidenceConnector,
    EvidenceReadAttempt,
    Investigation,
    InvestigationConnectorSnapshot,
    InvestigationDecision,
    InvestigationModelBindingSnapshot,
    InvestigationModelPolicySnapshot,
    InvestigationOperation,
    InvestigationStep,
    ModelPolicyRevision,
    ModelRoutingDecision,
    NativeReadCandidate,
    ProviderAccountModel,
    User,
    Workspace,
    WorkspaceArchitectureContextRevision,
    WorkspaceModelBinding,
)
from lode.db.session import AsyncSessionLocal, engine
from lode.development.isolated_database import require_isolated_database
from lode.domain.investigation import PlannedOperation
from lode.evidence_access.authorizer import EvidenceAccessAuthorizer
from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.mock import MockEvidenceAdapter
from lode.evidence_access.orchestrator import EvidenceReadOrchestrator, ExecutionPermit
from lode.evidence_access.tokens import AuthorizationTokenError, verify_token
from lode.evidence_access.types import AccessContext
from lode.evidence_connectors.registry import build_native_policy_registry
from lode.evidence_connectors.types import ProviderExecutionError
from lode.infrastructure.evidence_archive import PostgresEvidenceResultArchiver
from lode.infrastructure.intake_store import PostgresIntakeStore
from lode.infrastructure.native_read_executor import NativeReadOperationExecutor
from lode.model_catalog import require_model

SENTINEL = "__LODE_VALUE_REF_INCIDENT_TRACE__"
RAW_TRACE = ' trace/值?x=1&quoted="yes"\nnext '


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate(
    *,
    action_id: str,
    connector_id: int,
    language: str = "elasticsearch_query_dsl",
    variant: str = "default",
) -> NativeReadCandidateInput:
    if language in {"logql", "sql"}:
        payload = (
            {"query": f'{{app="api"}} |= "{SENTINEL}"'}
            if language == "logql"
            else {"query": "SELECT pg_sleep(1)" if variant == "unsupported" else "SELECT 1"}
        )
    else:
        query = {"term": {"trace.id": SENTINEL}}
        if variant in {"provider_failure", "bridge"}:
            query = {
                "bool": {
                    "filter": [
                        query,
                        {
                            "term": {
                                "level": "error" if variant == "provider_failure" else "warning"
                            }
                        },
                    ],
                }
            }
        payload = {
            "path": "/logs/_search",
            "body": {"query": query},
        }
    bindings = {} if language == "sql" else {SENTINEL: "incident.trace_id"}
    return NativeReadCandidateInput.model_validate(
        {
            "schema_version": "native-read-candidate.v1",
            "action_id": action_id,
            "connector_id": connector_id,
            "language": language,
            "purpose": "Find exact trace evidence",
            "expected_evidence": "One matching log record",
            "evidence_anchors": ["incident.trace_id"],
            "payload": payload,
            "value_bindings": bindings,
            "requested_window": {
                "start": "2026-08-26T11:50:00Z",
                "end": "2026-08-26T12:10:00Z",
            },
            "requested_limit": 2_000,
            "requested_timeout_ms": 60_000,
        }
    )


class SlowCaptureAdapter(MockEvidenceAdapter):
    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls

    async def execute(self, permit):
        permit.assert_valid()
        self.calls.append(dict(permit.action))
        await asyncio.sleep(0.05)
        return {"records": [{"trace": _find_trace_value(permit.action)}]}


class RateLimitedAdapter(MockEvidenceAdapter):
    async def execute(self, permit):
        permit.assert_valid()
        raise ProviderExecutionError("rate_limited", "fixture provider rate limit")


class StaticAdapterResolver:
    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls

    async def resolve(self, _session, _snapshot):
        return SlowCaptureAdapter(self.calls)


def _find_trace_value(value):
    if isinstance(value, dict):
        term = value.get("term")
        if isinstance(term, dict) and "trace.id" in term:
            return term["trace.id"]
        for item in value.values():
            found = _find_trace_value(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_trace_value(item)
            if found is not None:
                return found
    return None


async def _create_fixture(session):
    fixture_id = uuid4().hex[:12]
    user = User(
        username=f"evidence-{fixture_id}",
        display_name=f"Evidence Access Check {fixture_id}",
        password_hash="checker",
        status="active",
    )
    workspace = Workspace(
        name=f"Evidence access check {fixture_id}",
        ingestion_topic=f"evidence-access-check-{fixture_id}",
    )
    session.add_all([user, workspace])
    await session.flush()
    architecture_context = WorkspaceArchitectureContextRevision(
        workspace_id=workspace.id,
        entries=[],
        revision=1,
        created_by=user.id,
    )
    session.add(architecture_context)
    await session.flush()
    workspace.architecture_context_revision_id = architecture_context.id
    request = ManualIncidentRequest.model_validate(
        {
            "workspace_id": workspace.id,
            "occurred_at": "2026-08-26T12:00:00Z",
            "severity": "WARNING",
            "event": "evidence.access.check",
            "trace_id": RAW_TRACE,
            "source_revision": "a" * 40,
            "error": {"type": "Check", "message": "evidence", "stack": "frame", "cause": None},
        }
    )
    intake = await PostgresIntakeStore(session).persist_manual(
        workspace_id=workspace.id,
        incident=normalize_manual(request),
        created_by=user.id,
    )
    investigation = await session.get(Investigation, intake.investigation_id)

    provider = AIProviderAccount(
        name=f"evidence-access-check-{fixture_id}",
        provider_kind="openai",
        protocol_id="openai.responses.v1",
        base_url="https://model.invalid",
        api_key_ciphertext=encrypt_value("model-secret"),
        verification_status="healthy",
    )
    session.add(provider)
    await session.flush()
    profile = require_model("openai", "openai.responses.v1", "gpt-5.6-sol")
    deployment = ProviderAccountModel(
        provider_account_id=provider.id,
        provider_model_id=profile.model_id,
        catalog_revision=profile.catalog_revision,
        catalog_profile_hash=profile.profile_hash,
        discovery_state="manual",
        availability_state="healthy",
    )
    context_policy = ContextPolicyRevision(
        workspace_id=workspace.id,
        pinned_evidence_kinds=["investigation_input"],
        compression_levels=["full"],
        minimum_output_tokens=1_000,
        provider_safety_margin_tokens=256,
        revision=1,
    )
    session.add_all([deployment, context_policy])
    await session.flush()
    binding = WorkspaceModelBinding(
        workspace_id=workspace.id,
        provider_account_model_id=deployment.id,
        execution_classes=["latency_optimized"],
        allowed_roles=["native_query"],
        max_calls=10,
        max_cost_per_call=1,
        timeout_ms=30_000,
        allowed_data_classes=["masked"],
        max_context_utilization=0.8,
    )
    session.add(binding)
    await session.flush()
    model_policy = ModelPolicyRevision(
        workspace_id=workspace.id,
        eligible_bindings=[{"binding_id": binding.id, "revision": binding.revision}],
        role_policies={"native_query": {"binding_id": binding.id}},
        context_policy_revision_id=context_policy.id,
        revision=1,
    )
    session.add(model_policy)
    await session.flush()
    model_policy_hash = _hash("model-policy-snapshot")
    session.add(
        InvestigationModelPolicySnapshot(
            investigation_id=investigation.id,
            model_policy_revision_id=model_policy.id,
            context_policy_revision_id=context_policy.id,
            model_policy_revision=1,
            context_policy_revision=1,
            policy=model_policy.role_policies,
            context_policy={"pinned": context_policy.pinned_evidence_kinds},
            snapshot_hash=model_policy_hash,
        )
    )
    binding_snapshot = InvestigationModelBindingSnapshot(
        investigation_id=investigation.id,
        workspace_model_binding_id=binding.id,
        provider_account_model_id=deployment.id,
        provider_account_id=provider.id,
        binding_revision=1,
        provider_account_model_revision=1,
        provider_account_revision=1,
        execution_classes=["latency_optimized"],
        allowed_roles=["native_query"],
        routing_policy={"priority": 0},
        snapshot_hash=_hash("binding-snapshot"),
    )
    session.add(binding_snapshot)
    await session.flush()
    routing = ModelRoutingDecision(
        investigation_id=investigation.id,
        role="native_query",
        model_binding_snapshot_id=binding_snapshot.id,
        execution_class="latency_optimized",
        required_context_tokens=100,
        allowed_input_tokens=1_000,
        allowed_output_tokens=500,
        excluded_candidates=[],
        selection_reason="test",
        budget={"max_calls": 10},
        decision_hash=_hash("routing"),
    )
    session.add(routing)
    await session.flush()
    bundle = ContextBundleRevision(
        investigation_id=investigation.id,
        routing_decision_id=routing.id,
        role="native_query",
        revision=1,
        state_packet={"phase": "test"},
        evidence_refs=[],
        summary_refs=[],
        pinned_evidence_refs=[],
        tokenizer_id="mock",
        token_count=100,
        reserved_output_tokens=500,
        provider_safety_margin_tokens=100,
        context_hash=_hash("context"),
    )
    session.add(bundle)

    connector = EvidenceConnector(
        workspace_id=workspace.id,
        name="mock-search",
        kind="elasticsearch",
        kind_version=1,
        config={"endpoint": "https://logs.invalid"},
        secret_ciphertext=encrypt_value("connector-secret"),
        instance_revision=1,
        verification_status="healthy",
        verified_at=datetime.now(UTC),
        capabilities=["query"],
    )
    session.add(connector)
    await session.flush()
    scope = EvidenceAccessScope(
        connector_id=connector.id,
        allowed_languages=["elasticsearch_query_dsl", "sql"],
        scope_config={
            "allowed_indices": ["logs"],
            "required_terms": {},
            "timestamp_field": "@timestamp",
            "allowed_source_fields": ["@timestamp", "message", "trace.id"],
            "default_source_fields": ["@timestamp", "message", "trace.id"],
            "max_page_size": 100,
        },
        schema_catalog={
            "indices": {
                "logs": {
                    "fields": {
                        "@timestamp": {
                            "type": "date",
                            "searchable": True,
                            "aggregatable": True,
                        },
                        "message": {
                            "type": "text",
                            "searchable": True,
                            "aggregatable": False,
                        },
                        "trace.id": {
                            "type": "keyword",
                            "searchable": True,
                            "aggregatable": True,
                            "cardinality": 100,
                        },
                        "level": {
                            "type": "keyword",
                            "searchable": True,
                            "aggregatable": True,
                            "cardinality": 6,
                        },
                    }
                }
            }
        },
        schema_catalog_revision=1,
        read_policy_revision=1,
        execution_budget_policy={
            "max_result_limit": 500,
            "max_timeout_ms": 10_000,
            "max_output_bytes": 1_000_000,
            "max_total_output_bytes": 2_000_000,
            "max_window_seconds": 900,
            "max_native_reads": 8,
        },
        normalization_policy_revision=1,
        revision=1,
    )
    session.add(scope)
    await session.flush()
    connector_snapshot = InvestigationConnectorSnapshot(
        investigation_id=investigation.id,
        connector_id=connector.id,
        access_scope_id=scope.id,
        connector_kind=connector.kind,
        connector_kind_version=1,
        instance_revision=1,
        access_scope_revision=1,
        verification_status="healthy",
        verified_at=datetime.now(UTC),
        last_introspected_at=datetime.now(UTC),
        capabilities=["query"],
        allowed_languages=scope.allowed_languages,
        config_masked=connector.config,
        scope_config=scope.scope_config,
        schema_catalog=scope.schema_catalog,
        execution_budget_policy=scope.execution_budget_policy,
        credential_identity_hash=_hash("connector-credential"),
        snapshot_hash=_hash("connector-snapshot"),
    )
    session.add(connector_snapshot)
    await session.flush()

    step = InvestigationStep(
        investigation_id=investigation.id,
        ordinal=1,
        objective="Test native access",
        status="running",
        hypothesis_snapshot={"id": "h1"},
        input_evidence_refs=[],
        output_evidence_refs=[],
    )
    session.add(step)
    await session.flush()
    decision = InvestigationDecision(
        investigation_id=investigation.id,
        step_id=step.id,
        ordinal=1,
        decision="continue",
        hypotheses=[{"id": "h1"}],
        operation_plan=[{"action_id": f"evidence.check.{index}"} for index in range(1, 5)],
        policy_outcome="allow",
        policy_decisions=[],
        selected_operation_count=4,
        decision_hash=_hash("investigation-decision"),
    )
    session.add(decision)
    await session.flush()
    operations = []
    invocations = []
    for index in range(1, 5):
        operation = InvestigationOperation(
            investigation_id=investigation.id,
            step_id=step.id,
            decision_id=decision.id,
            ordinal=index,
            wave_ordinal=index,
            action_id=f"evidence.check.{index}",
            operation_kind="native_read",
            purpose="Test native access",
            expected_evidence="Mock record",
            evidence_anchors=["incident.trace_id"],
            selection_reason="test",
            stop_condition="record found",
            input_masked={},
            fingerprint=_hash(f"operation-{index}"),
        )
        session.add(operation)
        await session.flush()
        invocation = AIInvocation(
            investigation_id=investigation.id,
            operation_id=operation.id,
            routing_decision_id=routing.id,
            context_bundle_revision_id=bundle.id,
            role="native_query",
            provider_account_id=provider.id,
            provider_account_model_id=deployment.id,
            provider_account_revision=1,
            provider_account_model_revision=1,
            execution_class="latency_optimized",
            prompt_revision="test",
            schema_revision="native-read-candidate.v1",
            context_hash=bundle.context_hash,
            request_hash=_hash(f"request-{index}"),
            response_hash=_hash(f"response-{index}"),
            status="succeeded",
            attempt_count=1,
            latency_ms=1,
            output_masked={"candidate": index},
        )
        session.add(invocation)
        await session.flush()
        operations.append(operation)
        invocations.append(invocation)

    step.status = "succeeded"
    step.finished_at = datetime.now(UTC)
    await session.flush()
    second_step = InvestigationStep(
        investigation_id=investigation.id,
        ordinal=2,
        objective="Test provider failure audit",
        status="running",
        hypothesis_snapshot={"id": "h2"},
        input_evidence_refs=[],
        output_evidence_refs=[],
    )
    session.add(second_step)
    await session.flush()
    second_decision = InvestigationDecision(
        investigation_id=investigation.id,
        step_id=second_step.id,
        ordinal=2,
        decision="continue",
        hypotheses=[{"id": "h2"}],
        operation_plan=[{"action_id": "evidence.check.5"}],
        policy_outcome="allow",
        policy_decisions=[],
        selected_operation_count=1,
        decision_hash=_hash("investigation-decision-2"),
    )
    session.add(second_decision)
    await session.flush()
    failure_operation = InvestigationOperation(
        investigation_id=investigation.id,
        step_id=second_step.id,
        decision_id=second_decision.id,
        ordinal=5,
        wave_ordinal=1,
        action_id="evidence.check.5",
        operation_kind="native_read",
        purpose="Test provider failure audit",
        expected_evidence="Stable provider failure",
        evidence_anchors=["incident.trace_id"],
        selection_reason="test",
        stop_condition="failure recorded",
        input_masked={},
        fingerprint=_hash("operation-5"),
    )
    session.add(failure_operation)
    await session.flush()
    failure_invocation = AIInvocation(
        investigation_id=investigation.id,
        operation_id=failure_operation.id,
        routing_decision_id=routing.id,
        context_bundle_revision_id=bundle.id,
        role="native_query",
        provider_account_id=provider.id,
        provider_account_model_id=deployment.id,
        provider_account_revision=1,
        provider_account_model_revision=1,
        execution_class="latency_optimized",
        prompt_revision="test",
        schema_revision="native-read-candidate.v1",
        context_hash=bundle.context_hash,
        request_hash=_hash("request-5"),
        response_hash=_hash("response-5"),
        status="succeeded",
        attempt_count=1,
        latency_ms=1,
        output_masked={"candidate": 5},
    )
    session.add(failure_invocation)
    await session.flush()
    operations.append(failure_operation)
    invocations.append(failure_invocation)

    second_step.status = "succeeded"
    second_step.finished_at = datetime.now(UTC)
    bridge_step = InvestigationStep(
        investigation_id=investigation.id,
        ordinal=3,
        objective="Test durable native operation bridge",
        status="running",
        hypothesis_snapshot={"id": "h3"},
        input_evidence_refs=[],
        output_evidence_refs=[],
    )
    session.add(bridge_step)
    await session.flush()
    bridge_decision = InvestigationDecision(
        investigation_id=investigation.id,
        step_id=bridge_step.id,
        ordinal=3,
        decision="continue",
        hypotheses=[{"id": "h3"}],
        operation_plan=[{"action_id": "evidence.check.6"}],
        policy_outcome="allow",
        policy_decisions=[],
        selected_operation_count=1,
        decision_hash=_hash("investigation-decision-3"),
    )
    session.add(bridge_decision)
    await session.flush()
    bridge_operation = InvestigationOperation(
        investigation_id=investigation.id,
        step_id=bridge_step.id,
        decision_id=bridge_decision.id,
        ordinal=6,
        wave_ordinal=1,
        action_id="evidence.check.6",
        operation_kind="native_read",
        purpose="Test durable native operation bridge",
        expected_evidence="Archived provider result",
        evidence_anchors=["incident.trace_id"],
        selection_reason="test",
        stop_condition="result archived",
        input_masked={},
        fingerprint=_hash("operation-6"),
    )
    session.add(bridge_operation)
    await session.flush()
    bridge_invocation = AIInvocation(
        investigation_id=investigation.id,
        operation_id=bridge_operation.id,
        routing_decision_id=routing.id,
        context_bundle_revision_id=bundle.id,
        role="native_query",
        provider_account_id=provider.id,
        provider_account_model_id=deployment.id,
        provider_account_revision=1,
        provider_account_model_revision=1,
        execution_class="latency_optimized",
        prompt_revision="test",
        schema_revision="native-read-candidate.v1",
        context_hash=bundle.context_hash,
        request_hash=_hash("request-6"),
        response_hash=_hash("response-6"),
        status="succeeded",
        attempt_count=1,
        latency_ms=1,
        output_masked={"candidate": 6},
    )
    session.add(bridge_invocation)
    await session.flush()
    bridge_decision.model_invocation_id = bridge_invocation.id
    operations.append(bridge_operation)
    invocations.append(bridge_invocation)
    await session.commit()
    return workspace, investigation, connector, connector_snapshot, operations, invocations


def _context(workspace, investigation, connector, connector_snapshot, operation, invocation):
    return AccessContext(
        investigation_id=investigation.id,
        operation_id=operation.id,
        connector_snapshot_id=connector_snapshot.id,
        model_invocation_id=invocation.id,
        workspace_id=workspace.id,
        connector_id=connector.id,
        snapshot_hash=connector_snapshot.snapshot_hash,
        allowed_languages=tuple(connector_snapshot.allowed_languages),
        allowed_evidence_anchors=("incident.trace_id", "assertion:h1"),
        scope_config=connector_snapshot.scope_config,
        schema_catalog=connector_snapshot.schema_catalog,
        execution_budget_policy=connector_snapshot.execution_budget_policy,
        investigation_window_start=investigation.window_started_at,
        investigation_window_end=investigation.window_finished_at,
    )


async def main() -> None:
    require_isolated_database("evidence access check")
    async with AsyncSessionLocal() as session:
        fixture = await _create_fixture(session)
        workspace, investigation, connector, snapshot, operations, invocations = fixture
        registry = build_native_policy_registry()

        allow = await EvidenceAccessAuthorizer(session, registry).authorize(
            _candidate(action_id=operations[0].action_id, connector_id=connector.id),
            _context(workspace, investigation, connector, snapshot, operations[0], invocations[0]),
        )
        assert allow.outcome == "allow" and allow.token and allow.authorized_read_id
        authorized = await session.get(AuthorizedEvidenceRead, allow.authorized_read_id)
        bound = json.loads(decrypt_value(authorized.effective_action_ciphertext))
        assert _find_trace_value(bound) == RAW_TRACE
        candidate_row = await session.get(NativeReadCandidate, allow.candidate_id)
        decision_row = await session.get(EvidenceAccessDecision, allow.decision_id)
        assert RAW_TRACE not in json.dumps(candidate_row.payload_masked, ensure_ascii=False)
        assert RAW_TRACE not in json.dumps(decision_row.effective_action_masked, ensure_ascii=False)
        assert decision_row.effective_budget["result_limit"] == 500
        assert decision_row.effective_budget["timeout_ms"] == 10_000

        unsupported = await EvidenceAccessAuthorizer(session, registry).authorize(
            _candidate(
                action_id=operations[1].action_id,
                connector_id=connector.id,
                language="sql",
                variant="unsupported",
            ),
            _context(workspace, investigation, connector, snapshot, operations[1], invocations[1]),
        )
        assert unsupported.rejection_code == "unsupported_node"

        connector.state = "disabled"
        await session.flush()
        disabled = await EvidenceAccessAuthorizer(session, registry).authorize(
            _candidate(action_id=operations[2].action_id, connector_id=connector.id),
            _context(workspace, investigation, connector, snapshot, operations[2], invocations[2]),
        )
        assert disabled.rejection_code == "scope_violation"
        connector.state = "active"
        await session.flush()

        duplicate = await EvidenceAccessAuthorizer(session, registry).authorize(
            _candidate(action_id=operations[3].action_id, connector_id=connector.id),
            _context(workspace, investigation, connector, snapshot, operations[3], invocations[3]),
        )
        assert duplicate.rejection_code == "budget_violation"

        provider_failure = await EvidenceAccessAuthorizer(session, registry).authorize(
            _candidate(
                action_id=operations[4].action_id,
                connector_id=connector.id,
                variant="provider_failure",
            ),
            _context(workspace, investigation, connector, snapshot, operations[4], invocations[4]),
        )
        assert provider_failure.outcome == "allow" and provider_failure.token

    calls: list[dict] = []
    async with AsyncSessionLocal() as first_session, AsyncSessionLocal() as second_session:
        outcomes = await asyncio.gather(
            EvidenceReadOrchestrator(
                first_session, PostgresEvidenceResultArchiver(first_session)
            ).execute(allow.token, SlowCaptureAdapter(calls)),
            EvidenceReadOrchestrator(
                second_session, PostgresEvidenceResultArchiver(second_session)
            ).execute(allow.token, SlowCaptureAdapter(calls)),
            return_exceptions=True,
        )
    assert sum(getattr(item, "status", None) == "succeeded" for item in outcomes) == 1
    assert sum(isinstance(item, AuthorizationTokenError) for item in outcomes) == 1
    assert len(calls) == 1
    assert _find_trace_value(calls[0]) == RAW_TRACE

    async with AsyncSessionLocal() as failure_session:
        failed = await EvidenceReadOrchestrator(
            failure_session, PostgresEvidenceResultArchiver(failure_session)
        ).execute(
            provider_failure.token,
            RateLimitedAdapter(),
        )
    assert failed.status == "failed" and failed.failure_code == "rate_limited"

    bridge_calls: list[dict] = []
    bridge_candidate = _candidate(
        action_id=operations[5].action_id,
        connector_id=connector.id,
        variant="bridge",
    )
    bridge_result = await NativeReadOperationExecutor(
        AsyncSessionLocal, StaticAdapterResolver(bridge_calls)
    ).execute(
        operations[5].id,
        PlannedOperation(
            action_id=operations[5].action_id,
            purpose=bridge_candidate.purpose,
            expected_evidence=bridge_candidate.expected_evidence,
            evidence_anchors=tuple(bridge_candidate.evidence_anchors),
            supports_hypotheses=("h3",),
            refutes_hypotheses=(),
            selection_reason="Exercise the durable native operation bridge",
            stop_condition="Stop after the result is archived",
            estimated_cost=0.0,
            native_candidate=bridge_candidate.model_dump(mode="json"),
        ),
    )
    assert bridge_result.status == "succeeded"
    assert len(bridge_result.evidence_refs) == 1
    assert len(bridge_calls) == 1
    assert _find_trace_value(bridge_calls[0]) == RAW_TRACE

    bad_permit = ExecutionPermit(1, 1, {}, "a" * 64, object())
    try:
        await MockEvidenceAdapter().execute(bad_permit)
    except PermissionError:
        pass
    else:
        raise AssertionError("adapter accepted a forged execution permit")

    claims = verify_token(
        allow.token,
        key=__import__("lode.config", fromlist=["settings"]).settings.evidence_authorization_key,
    )
    assert claims["candidate_hash"] == authorized.candidate_hash
    async with AsyncSessionLocal() as session:
        failure_attempt = (
            await session.execute(
                select(EvidenceReadAttempt).where(
                    EvidenceReadAttempt.authorized_read_id == provider_failure.authorized_read_id
                )
            )
        ).scalar_one()
        assert failure_attempt.status == "failed"
        assert failure_attempt.failure_code == "rate_limited"
        success_attempt = (
            await session.execute(
                select(EvidenceReadAttempt).where(
                    EvidenceReadAttempt.authorized_read_id == allow.authorized_read_id
                )
            )
        ).scalar_one()
        assert success_attempt.status == "succeeded"
        assert len(success_attempt.result_artifact_refs) == 1
        assert (
            await session.get(EvidenceArtifact, success_attempt.result_artifact_refs[0]) is not None
        )
        counts = {
            "authorized_reads": (
                await session.execute(
                    select(func.count())
                    .select_from(AuthorizedEvidenceRead)
                    .where(AuthorizedEvidenceRead.investigation_id == investigation.id)
                )
            ).scalar_one(),
            "candidates": (
                await session.execute(
                    select(func.count())
                    .select_from(NativeReadCandidate)
                    .where(NativeReadCandidate.investigation_id == investigation.id)
                )
            ).scalar_one(),
            "decisions": (
                await session.execute(
                    select(func.count())
                    .select_from(EvidenceAccessDecision)
                    .where(EvidenceAccessDecision.investigation_id == investigation.id)
                )
            ).scalar_one(),
            "attempts": (
                await session.execute(
                    select(func.count())
                    .select_from(EvidenceReadAttempt)
                    .where(EvidenceReadAttempt.investigation_id == investigation.id)
                )
            ).scalar_one(),
        }
        print(
            json.dumps(
                {
                    **counts,
                    "allow": allow.outcome,
                    "bound_trace_round_trip": _find_trace_value(calls[0]) == RAW_TRACE,
                    "durable_bridge_archived": len(bridge_result.evidence_refs) == 1,
                    "concurrent_adapter_calls": len(calls),
                    "duplicate_fingerprint": duplicate.rejection_code,
                    "connector_lifecycle": disabled.rejection_code,
                    "replay_rejected": any(
                        isinstance(item, AuthorizationTokenError) for item in outcomes
                    ),
                    "unsupported_sql": unsupported.rejection_code,
                    "provider_failure": failed.failure_code,
                },
                indent=2,
                sort_keys=True,
            )
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
