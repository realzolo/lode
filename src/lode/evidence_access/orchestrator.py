"""Token-gated execution boundary with concurrent replay prevention."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lode.application.intake import canonical_hash
from lode.config import settings
from lode.crypto import decrypt_value
from lode.db.models import AuthorizedEvidenceRead, EvidenceAccessDecision, EvidenceReadAttempt
from lode.evidence_access.tokens import AuthorizationTokenError, token_hash, verify_token
from lode.evidence_access.types import EvidenceExecutionFailure

_PERMIT_AUTHORITY = object()


@dataclass(frozen=True, slots=True)
class ExecutionPermit:
    authorized_read_id: int
    investigation_id: int
    action: Mapping[str, Any]
    effective_action_hash: str
    _authority: object

    def assert_valid(self) -> None:
        if self._authority is not _PERMIT_AUTHORITY:
            raise PermissionError("invalid execution permit")


class EvidenceExecutionAdapter(Protocol):
    async def preflight(self, permit: ExecutionPermit) -> Mapping[str, Any]: ...
    async def execute(self, permit: ExecutionPermit) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: str
    attempt: int
    result: Mapping[str, Any] | None
    failure_code: str | None


class EvidenceReadOrchestrator:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        token: str,
        adapter: EvidenceExecutionAdapter,
    ) -> ExecutionResult:
        claims = verify_token(token, key=settings.evidence_authorization_key)
        row = (
            await self.session.execute(
                select(AuthorizedEvidenceRead, EvidenceAccessDecision)
                .join(
                    EvidenceAccessDecision,
                    EvidenceAccessDecision.id == AuthorizedEvidenceRead.access_decision_id,
                )
                .where(AuthorizedEvidenceRead.token_hash == token_hash(token))
            )
        ).one_or_none()
        if row is None:
            raise AuthorizationTokenError("authorization token is unknown")
        authorized, decision = row
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": authorized.id},
        )
        self._verify_claims(claims, authorized, decision)
        previous = (
            await self.session.execute(
                select(func.count())
                .select_from(EvidenceReadAttempt)
                .where(EvidenceReadAttempt.authorized_read_id == authorized.id)
            )
        ).scalar_one()
        if previous:
            raise AuthorizationTokenError("authorization token was already consumed")
        action_text = decrypt_value(authorized.effective_action_ciphertext)
        action = json.loads(action_text)
        if canonical_hash(action) != authorized.effective_action_hash:
            raise AuthorizationTokenError("authorized action ciphertext hash mismatch")
        permit = ExecutionPermit(
            authorized_read_id=authorized.id,
            investigation_id=authorized.investigation_id,
            action=action,
            effective_action_hash=authorized.effective_action_hash,
            _authority=_PERMIT_AUTHORITY,
        )
        started_at = datetime.now(UTC)
        preflight: Mapping[str, Any] | None = None
        try:
            preflight = await adapter.preflight(permit)
            if not isinstance(preflight, Mapping):
                raise TypeError("preflight result must be a mapping")
            result = await adapter.execute(permit)
            if not isinstance(result, Mapping):
                raise TypeError("evidence result must be a mapping")
            result_bytes = len(json.dumps(result, ensure_ascii=False).encode())
            output_limit = int(decision.effective_budget["output_bytes"])
            if result_bytes > output_limit:
                raise ValueError("evidence result exceeds authorized output byte limit")
            finished_at = datetime.now(UTC)
            self.session.add(
                EvidenceReadAttempt(
                    investigation_id=authorized.investigation_id,
                    authorized_read_id=authorized.id,
                    attempt=1,
                    status="succeeded",
                    preflight=dict(preflight),
                    started_at=started_at,
                    finished_at=finished_at,
                    result_artifact_refs=[],
                    metrics={"result_bytes": result_bytes},
                )
            )
            await self.session.commit()
            return ExecutionResult("succeeded", 1, result, None)
        except asyncio.CancelledError:
            finished_at = datetime.now(UTC)
            self.session.add(
                EvidenceReadAttempt(
                    investigation_id=authorized.investigation_id,
                    authorized_read_id=authorized.id,
                    attempt=1,
                    status="interrupted",
                    preflight=None if preflight is None else dict(preflight),
                    started_at=started_at,
                    finished_at=finished_at,
                    result_artifact_refs=[],
                    metrics={},
                    failure_code="execution_interrupted",
                    failure_detail={"reason": "task_cancelled"},
                )
            )
            await asyncio.shield(self.session.commit())
            raise
        except Exception as exc:  # noqa: BLE001 - every adapter failure needs a terminal audit row
            finished_at = datetime.now(UTC)
            failure_code = (
                exc.code
                if isinstance(exc, EvidenceExecutionFailure)
                else "preflight_failed"
                if preflight is None
                else "execution_failed"
            )
            failure_detail = (
                {"reason": exc.reason, **exc.detail}
                if isinstance(exc, EvidenceExecutionFailure)
                else {"error_type": type(exc).__name__, "message": str(exc)[:1000]}
            )
            self.session.add(
                EvidenceReadAttempt(
                    investigation_id=authorized.investigation_id,
                    authorized_read_id=authorized.id,
                    attempt=1,
                    status="failed",
                    preflight=None if preflight is None else dict(preflight),
                    started_at=started_at,
                    finished_at=finished_at,
                    result_artifact_refs=[],
                    metrics={},
                    failure_code=failure_code,
                    failure_detail=failure_detail,
                )
            )
            await self.session.commit()
            return ExecutionResult("failed", 1, None, failure_code)

    @staticmethod
    def _verify_claims(
        claims: Mapping[str, Any],
        authorized: AuthorizedEvidenceRead,
        decision: EvidenceAccessDecision,
    ) -> None:
        expected = {
            "investigation_id": authorized.investigation_id,
            "candidate_hash": authorized.candidate_hash,
            "decision_hash": decision.decision_hash,
            "snapshot_hash": authorized.snapshot_hash,
            "policy_hash": authorized.policy_hash,
            "effective_action_hash": authorized.effective_action_hash,
            "expires_at": authorized.expires_at.isoformat(),
        }
        for key, value in expected.items():
            if claims.get(key) != value:
                raise AuthorizationTokenError(f"authorization claim mismatch: {key}")
