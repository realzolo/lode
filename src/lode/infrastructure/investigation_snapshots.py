"""Freeze connector capabilities before an investigation can plan external reads."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lode.db.models import (
    EvidenceAccessScope,
    EvidenceConnector,
    Investigation,
    InvestigationConnectorSnapshot,
)
from lode.domain.investigation import (
    ConnectorCapabilitySnapshot,
    canonical_hash,
)
from lode.domain.types import NativeLanguage


class ConnectorSnapshotStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def freeze(self, investigation_id: int) -> tuple[InvestigationConnectorSnapshot, ...]:
        async with self.session_factory() as session:
            snapshots = await self.freeze_in_session(session, investigation_id)
            await session.commit()
            return snapshots

    @staticmethod
    async def freeze_in_session(
        session: AsyncSession, investigation_id: int
    ) -> tuple[InvestigationConnectorSnapshot, ...]:
        investigation = (
            await session.execute(
                select(Investigation).where(Investigation.id == investigation_id).with_for_update()
            )
        ).scalar_one()
        existing = tuple(
            (
                await session.execute(
                    select(InvestigationConnectorSnapshot)
                    .where(InvestigationConnectorSnapshot.investigation_id == investigation_id)
                    .order_by(InvestigationConnectorSnapshot.id)
                )
            )
            .scalars()
            .all()
        )
        if existing:
            return existing

        connectors = tuple(
            (
                await session.execute(
                    select(EvidenceConnector)
                    .where(
                        EvidenceConnector.workspace_id == investigation.workspace_id,
                        EvidenceConnector.state == "active",
                        EvidenceConnector.verification_status == "healthy",
                    )
                    .order_by(EvidenceConnector.id)
                )
            )
            .scalars()
            .all()
        )
        snapshots: list[InvestigationConnectorSnapshot] = []
        for connector in connectors:
            if connector.last_introspected_at is None:
                continue
            scope = (
                await session.execute(
                    select(EvidenceAccessScope)
                    .where(EvidenceAccessScope.connector_id == connector.id)
                    .order_by(EvidenceAccessScope.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if scope is None:
                continue
            if connector.kind in {"postgresql", "mysql"} and not scope.scope_config.get(
                "allowed_tables"
            ):
                continue
            credential_hash = hashlib.sha256(
                connector.secret_ciphertext.encode("utf-8")
            ).hexdigest()
            payload = {
                "connector_id": connector.id,
                "access_scope_id": scope.id,
                "connector_kind": connector.kind,
                "connector_kind_version": connector.kind_version,
                "instance_revision": connector.instance_revision,
                "access_scope_revision": scope.revision,
                "verification_status": connector.verification_status,
                "verified_at": connector.verified_at,
                "last_introspected_at": connector.last_introspected_at,
                "capabilities": connector.capabilities,
                "allowed_languages": scope.allowed_languages,
                "config_masked": connector.config,
                "scope_config": scope.scope_config,
                "schema_catalog": scope.schema_catalog,
                "execution_budget_policy": scope.execution_budget_policy,
                "credential_identity_hash": credential_hash,
            }
            snapshot = InvestigationConnectorSnapshot(
                investigation_id=investigation_id,
                snapshot_hash=canonical_hash(payload),
                **payload,
            )
            session.add(snapshot)
            snapshots.append(snapshot)
        await session.flush()
        return tuple(snapshots)

    async def capabilities(self, investigation_id: int) -> tuple[ConnectorCapabilitySnapshot, ...]:
        rows = await self.freeze(investigation_id)
        return tuple(self._domain(row) for row in rows)

    @staticmethod
    def _domain(row: InvestigationConnectorSnapshot) -> ConnectorCapabilitySnapshot:
        return ConnectorCapabilitySnapshot(
            snapshot_id=row.id,
            connector_id=row.connector_id,
            connector_kind=row.connector_kind,
            connector_kind_version=row.connector_kind_version,
            allowed_languages=tuple(NativeLanguage(value) for value in row.allowed_languages),
            capabilities=tuple(row.capabilities),
            schema_catalog=row.schema_catalog,
            scope_config=row.scope_config,
            execution_budget_policy=row.execution_budget_policy,
            snapshot_hash=row.snapshot_hash,
            health_status=row.verification_status,
            last_verified_at=row.verified_at,
        )


def snapshot_ids(values: Sequence[ConnectorCapabilitySnapshot]) -> tuple[int, ...]:
    return tuple(value.snapshot_id for value in values)
