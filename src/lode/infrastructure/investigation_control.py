"""Transactional operator controls for immutable investigation runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lode.db.models import (
    IncidentEvent,
    Investigation,
    InvestigationControlEvent,
    InvestigationJob,
)
from lode.masking import mask_structure


@dataclass(frozen=True, slots=True)
class InvestigationControlResult:
    investigation_id: int
    status: Literal["paused", "cancelled"]


class InvestigationControlService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def stop(
        self,
        *,
        investigation_id: int,
        actor_id: int,
        command: Literal["pause", "cancel"],
        reason: str,
    ) -> InvestigationControlResult:
        investigation = (
            await self.session.execute(
                select(Investigation)
                .where(Investigation.id == investigation_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if investigation is None:
            raise ValueError("Investigation does not exist")
        if investigation.status not in {"queued", "running", "reporting"}:
            raise ValueError("Only an active investigation can be paused or cancelled")
        job = (
            await self.session.execute(
                select(InvestigationJob)
                .where(InvestigationJob.investigation_id == investigation.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.status not in {"pending", "running"}:
            raise ValueError("Investigation has no active job")

        status: Literal["paused", "cancelled"] = (
            "paused" if command == "pause" else "cancelled"
        )
        masked_payload, _ = mask_structure({"reason": reason})
        now = datetime.now(UTC)
        investigation.status = status
        investigation.finished_at = now
        job.status = status
        if job.claimed_by is None:
            job.lease_expires_at = None
            investigation.lease_owner = None
            investigation.lease_expires_at = None
        self.session.add(
            InvestigationControlEvent(
                investigation_id=investigation.id,
                command=command,
                actor_id=actor_id,
                payload_masked=masked_payload,
            )
        )
        self.session.add(
            IncidentEvent(
                incident_id=investigation.incident_id,
                event_type="investigation_controlled",
                actor_id=actor_id,
                payload={
                    "investigation_id": investigation.id,
                    "command": command,
                    "status": status,
                },
            )
        )
        await self.session.commit()
        return InvestigationControlResult(investigation.id, status)
