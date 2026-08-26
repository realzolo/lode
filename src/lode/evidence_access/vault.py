"""Investigation-scoped ValueRef resolution after policy authorization."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.crypto import decrypt_value
from lode.db.models import SealedEvidenceValue
from lode.evidence_access.types import AccessRejection


class EvidenceValueVault:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self,
        *,
        workspace_id: int,
        investigation_id: int,
        value_refs: Iterable[str],
    ) -> dict[str, str]:
        refs = tuple(value_refs)
        if len(refs) != len(set(refs)):
            raise AccessRejection("scope_violation", "duplicate ValueRef requested")
        if not refs:
            return {}
        rows = (await self.session.execute(
            select(SealedEvidenceValue).where(
                SealedEvidenceValue.workspace_id == workspace_id,
                SealedEvidenceValue.investigation_id == investigation_id,
                SealedEvidenceValue.value_ref.in_(refs),
            )
        )).scalars().all()
        by_ref = {row.value_ref: row for row in rows}
        missing = sorted(set(refs) - set(by_ref))
        if missing:
            raise AccessRejection(
                "scope_violation",
                "candidate references unavailable sealed values",
                {"missing_value_refs": missing},
            )
        return {ref: decrypt_value(by_ref[ref].value_ciphertext) for ref in refs}
