"""Durable fail-closed candidate evaluation and authorization issuance."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.intake import canonical_hash
from lode.config import settings
from lode.crypto import encrypt_value
from lode.db.models import (
    AIInvocation,
    AuthorizedEvidenceRead,
    EvidenceAccessDecision,
    Investigation,
    InvestigationConnectorSnapshot,
    InvestigationOperation,
    NativeReadCandidate,
)
from lode.evidence_access.candidate import NativeReadCandidateInput
from lode.evidence_access.kill_switch import EvidenceKillSwitch
from lode.evidence_access.registry import NativePolicyRegistry
from lode.evidence_access.tokens import issue_token, token_hash
from lode.evidence_access.types import AccessContext, AccessRejection, AuthorizedReadResult
from lode.evidence_access.vault import EvidenceValueVault


def _call_policy[PolicyResult](
    stage: str, operation: Callable[[], PolicyResult]
) -> PolicyResult:
    """Turn candidate-driven parser failures into durable, non-sensitive rejects."""
    try:
        return operation()
    except AccessRejection:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise AccessRejection(
            "invalid_syntax",
            "native action could not be safely processed by the active policy",
            {"stage": stage, "error_type": type(exc).__name__},
        ) from exc


class EvidenceAccessAuthorizer:
    policy_version = "evidence-access-kernel.1"

    def __init__(
        self,
        session: AsyncSession,
        registry: NativePolicyRegistry,
        kill_switch: EvidenceKillSwitch | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.kill_switch = kill_switch or EvidenceKillSwitch()

    async def authorize(
        self,
        candidate: NativeReadCandidateInput,
        context: AccessContext,
    ) -> AuthorizedReadResult:
        candidate_payload = candidate.model_dump(mode="json")
        candidate_hash = canonical_hash(candidate_payload)
        candidate_row = NativeReadCandidate(
            investigation_id=context.investigation_id,
            operation_id=context.operation_id,
            connector_snapshot_id=context.connector_snapshot_id,
            model_invocation_id=context.model_invocation_id,
            schema_version=candidate.schema_version,
            action_id=candidate.action_id,
            language=candidate.language,
            purpose=candidate.purpose,
            expected_evidence=candidate.expected_evidence,
            evidence_anchors=candidate.evidence_anchors,
            payload_masked=candidate.payload.model_dump(mode="json"),
            value_bindings=candidate.value_bindings,
            requested_window=(
                None
                if candidate.requested_window is None
                else candidate.requested_window.model_dump(mode="json")
            ),
            requested_limit=candidate.requested_limit,
            requested_timeout_ms=candidate.requested_timeout_ms,
            candidate_hash=candidate_hash,
        )
        self.session.add(candidate_row)
        await self.session.flush()

        parser_name = "unavailable"
        parser_version = "0"
        parse_tree_hash = canonical_hash({"unparsed_candidate_hash": candidate_hash})
        snapshot_authorization_hash = canonical_hash(
            {
                "investigation_id": context.investigation_id,
                "connector_snapshot_id": context.connector_snapshot_id,
                "connector_id": context.connector_id,
                "snapshot_hash": context.snapshot_hash,
                "allowed_languages": context.allowed_languages,
                "scope_config": context.scope_config,
                "schema_catalog": context.schema_catalog,
                "execution_budget_policy": context.execution_budget_policy,
            }
        )
        decisions: list[dict[str, Any]] = []
        try:
            await self._check_ownership(candidate, context)
            decisions.append({"check": "snapshot_ownership", "outcome": "allow"})
            self.kill_switch.check(
                workspace_id=context.workspace_id,
                connector_id=context.connector_id,
                language=candidate.language,
            )
            decisions.append({"check": "kill_switch", "outcome": "allow"})
            policy = self.registry.require(candidate.language)
            parser_name = policy.parser_name
            parser_version = policy.parser_version
            action = _call_policy("parse", lambda: policy.parse(candidate))
            parse_tree_hash = action.parse_tree_hash
            decisions.append({"check": "complete_parse", "outcome": "allow"})
            evaluation = _call_policy(
                "evaluate",
                lambda: policy.evaluate(action, candidate, context),
            )
            decisions.extend(dict(item) for item in evaluation.validation_decisions)
            values_by_sentinel = await self._resolve_bindings(candidate, context)
            bound = _call_policy(
                "bind_values",
                lambda: policy.bind_values(action, evaluation, values_by_sentinel),
            )
            if bound.structural_hash != evaluation.effective_structural_hash:
                raise AccessRejection("invalid_syntax", "bound action changed parsed structure")
            decisions.append({"check": "value_binding_reparse", "outcome": "allow"})
        except AccessRejection as exc:
            decisions.append({"check": "rejected", "outcome": "reject", "code": exc.code})
            decision = await self._persist_rejection(
                candidate_row=candidate_row,
                context=context,
                parser_name=parser_name,
                parser_version=parser_version,
                parse_tree_hash=parse_tree_hash,
                snapshot_authorization_hash=snapshot_authorization_hash,
                decisions=decisions,
                rejection=exc,
            )
            await self.session.commit()
            return AuthorizedReadResult(
                outcome="reject",
                candidate_id=candidate_row.id,
                decision_id=decision.id,
                rejection_code=exc.code,
                rejection_detail={"reason": exc.reason, **exc.detail},
            )

        budget = asdict(evaluation.effective_budget)
        for field in ("window_start", "window_end"):
            value = budget[field]
            budget[field] = None if value is None else value.isoformat()
        effective_masked = dict(evaluation.effective_action)
        language_policy_hash = canonical_hash(
            {
                "kernel": self.policy_version,
                "language": candidate.language,
                "parser_name": policy.parser_name,
                "parser_version": policy.parser_version,
                "policy_version": policy.policy_version,
                "snapshot_authorization_hash": snapshot_authorization_hash,
            }
        )
        effective_action = dict(bound.canonical_action)
        effective_action_hash = canonical_hash(effective_action)
        fingerprint = canonical_hash(
            {
                "connector_snapshot_id": context.connector_snapshot_id,
                "language": candidate.language,
                "effective_action_hash": effective_action_hash,
                "effective_budget": budget,
            }
        )
        existing_fingerprint = (
            await self.session.execute(
                select(AuthorizedEvidenceRead.id).where(
                    AuthorizedEvidenceRead.investigation_id == context.investigation_id,
                    AuthorizedEvidenceRead.fingerprint == fingerprint,
                )
            )
        ).scalar_one_or_none()
        if existing_fingerprint is not None:
            rejection = AccessRejection(
                "budget_violation",
                "an identical effective native read was already authorized",
                {"existing_authorized_read_id": existing_fingerprint},
            )
            decision = await self._persist_rejection(
                candidate_row=candidate_row,
                context=context,
                parser_name=policy.parser_name,
                parser_version=policy.parser_version,
                parse_tree_hash=parse_tree_hash,
                snapshot_authorization_hash=snapshot_authorization_hash,
                decisions=[*decisions, {"check": "fingerprint", "outcome": "reject"}],
                rejection=rejection,
            )
            await self.session.commit()
            return AuthorizedReadResult(
                outcome="reject",
                candidate_id=candidate_row.id,
                decision_id=decision.id,
                rejection_code=rejection.code,
                rejection_detail={"reason": rejection.reason, **rejection.detail},
            )

        decision_hash = canonical_hash(
            {
                "candidate_hash": candidate_hash,
                "snapshot_authorization_hash": snapshot_authorization_hash,
                "parse_tree_hash": parse_tree_hash,
                "policy_hash": language_policy_hash,
                "effective_action": effective_masked,
                "effective_budget": budget,
                "constraint_diff": evaluation.constraint_diff,
            }
        )
        decision = EvidenceAccessDecision(
            investigation_id=context.investigation_id,
            candidate_id=candidate_row.id,
            outcome="allow",
            parser_name=policy.parser_name,
            parser_version=policy.parser_version,
            policy_version=f"{self.policy_version}/{policy.policy_version}",
            parse_tree_hash=parse_tree_hash,
            snapshot_authorization_hash=snapshot_authorization_hash,
            validation_decisions=decisions,
            effective_action_masked=effective_masked,
            effective_budget=budget,
            constraint_diff=dict(evaluation.constraint_diff),
            decision_hash=decision_hash,
        )
        self.session.add(decision)
        await self.session.flush()

        if settings.evidence_authorization_ttl_seconds <= 0:
            raise RuntimeError("LODE_EVIDENCE_AUTHORIZATION_TTL_SECONDS must be positive")
        if settings.evidence_authorization_key in {
            "",
            settings.secret_key,
            settings.data_encryption_key,
        }:
            raise RuntimeError(
                "LODE_EVIDENCE_AUTHORIZATION_KEY must be independent from signing and encryption keys"
            )
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=settings.evidence_authorization_ttl_seconds)
        claims = {
            "investigation_id": context.investigation_id,
            "candidate_hash": candidate_hash,
            "decision_hash": decision_hash,
            "snapshot_hash": context.snapshot_hash,
            "policy_hash": decision_hash,
            "effective_action_hash": effective_action_hash,
            "expires_at": expires_at.isoformat(),
        }
        token = issue_token(claims, key=settings.evidence_authorization_key)
        authorized = AuthorizedEvidenceRead(
            investigation_id=context.investigation_id,
            access_decision_id=decision.id,
            candidate_hash=candidate_hash,
            snapshot_hash=context.snapshot_hash,
            policy_hash=decision_hash,
            effective_action_ciphertext=encrypt_value(
                json.dumps(
                    effective_action, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            ),
            effective_action_hash=effective_action_hash,
            fingerprint=fingerprint,
            token_hash=token_hash(token),
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self.session.add(authorized)
        await self.session.flush()
        await self.session.commit()
        return AuthorizedReadResult(
            outcome="allow",
            candidate_id=candidate_row.id,
            decision_id=decision.id,
            authorized_read_id=authorized.id,
            token=token,
            fingerprint=fingerprint,
        )

    async def _check_ownership(
        self,
        candidate: NativeReadCandidateInput,
        context: AccessContext,
    ) -> None:
        investigation = await self.session.get(Investigation, context.investigation_id)
        operation = await self.session.get(InvestigationOperation, context.operation_id)
        snapshot = await self.session.get(
            InvestigationConnectorSnapshot, context.connector_snapshot_id
        )
        invocation = await self.session.get(AIInvocation, context.model_invocation_id)
        if investigation is None or investigation.workspace_id != context.workspace_id:
            raise AccessRejection("scope_violation", "investigation does not belong to Workspace")
        if (
            operation is None
            or operation.investigation_id != investigation.id
            or operation.operation_kind != "native_read"
            or operation.action_id != candidate.action_id
        ):
            raise AccessRejection(
                "scope_violation", "candidate does not belong to native-read operation"
            )
        if (
            invocation is None
            or invocation.investigation_id != investigation.id
            or invocation.operation_id != operation.id
            or invocation.role != "native_query"
        ):
            raise AccessRejection(
                "scope_violation", "candidate model invocation is not operation-bound"
            )
        if snapshot is None or snapshot.investigation_id != investigation.id:
            raise AccessRejection(
                "scope_violation", "connector snapshot does not belong to investigation"
            )
        snapshot_values = {
            "connector_id": snapshot.connector_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "allowed_languages": tuple(snapshot.allowed_languages),
            "scope_config": snapshot.scope_config,
            "schema_catalog": snapshot.schema_catalog,
            "execution_budget_policy": snapshot.execution_budget_policy,
        }
        context_values = {
            "connector_id": context.connector_id,
            "snapshot_hash": context.snapshot_hash,
            "allowed_languages": context.allowed_languages,
            "scope_config": context.scope_config,
            "schema_catalog": context.schema_catalog,
            "execution_budget_policy": context.execution_budget_policy,
        }
        if snapshot_values != context_values:
            raise AccessRejection(
                "scope_violation", "authorization context differs from frozen snapshot"
            )
        if candidate.connector_id != context.connector_id:
            raise AccessRejection(
                "scope_violation", "connector is not the frozen snapshot connector"
            )
        if candidate.language not in context.allowed_languages:
            raise AccessRejection(
                "scope_violation", "language is not allowed by the connector snapshot"
            )
        allowed_anchors = set(context.allowed_evidence_anchors) & set(operation.evidence_anchors)
        if not set(candidate.evidence_anchors) & allowed_anchors:
            raise AccessRejection(
                "scope_violation", "candidate has no current-investigation evidence anchor"
            )

    async def _resolve_bindings(
        self,
        candidate: NativeReadCandidateInput,
        context: AccessContext,
    ) -> dict[str, str]:
        by_ref = await EvidenceValueVault(self.session).resolve(
            workspace_id=context.workspace_id,
            investigation_id=context.investigation_id,
            value_refs=candidate.value_bindings.values(),
        )
        return {
            sentinel: by_ref[value_ref] for sentinel, value_ref in candidate.value_bindings.items()
        }

    async def _persist_rejection(
        self,
        *,
        candidate_row: NativeReadCandidate,
        context: AccessContext,
        parser_name: str,
        parser_version: str,
        parse_tree_hash: str,
        snapshot_authorization_hash: str,
        decisions: list[dict[str, Any]],
        rejection: AccessRejection,
    ) -> EvidenceAccessDecision:
        detail = {"reason": rejection.reason, **rejection.detail}
        decision_hash = canonical_hash(
            {
                "candidate_hash": candidate_row.candidate_hash,
                "snapshot_authorization_hash": snapshot_authorization_hash,
                "parse_tree_hash": parse_tree_hash,
                "outcome": "reject",
                "rejection_code": rejection.code,
                "rejection_detail": detail,
            }
        )
        decision = EvidenceAccessDecision(
            investigation_id=context.investigation_id,
            candidate_id=candidate_row.id,
            outcome="reject",
            parser_name=parser_name,
            parser_version=parser_version,
            policy_version=self.policy_version,
            parse_tree_hash=parse_tree_hash,
            snapshot_authorization_hash=snapshot_authorization_hash,
            validation_decisions=decisions,
            rejection_code=rejection.code,
            rejection_detail=detail,
            decision_hash=decision_hash,
        )
        self.session.add(decision)
        await self.session.flush()
        return decision
