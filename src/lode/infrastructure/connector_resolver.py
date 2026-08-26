"""Resolve an execution adapter only from a frozen connector identity."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.ext.asyncio import AsyncSession

from lode.crypto import decrypt_value
from lode.db.models import EvidenceConnector, InvestigationConnectorSnapshot
from lode.evidence_access.orchestrator import EvidenceExecutionAdapter
from lode.evidence_connectors.registry import create_evidence_connector


class PostgresConnectorAdapterResolver:
    async def resolve(
        self,
        session: AsyncSession,
        snapshot: InvestigationConnectorSnapshot,
    ) -> EvidenceExecutionAdapter:
        connector = await session.get(EvidenceConnector, snapshot.connector_id)
        if (
            connector is None
            or connector.kind != snapshot.connector_kind
            or connector.kind_version != snapshot.connector_kind_version
            or connector.instance_revision != snapshot.instance_revision
            or hashlib.sha256(connector.secret_ciphertext.encode()).hexdigest()
            != snapshot.credential_identity_hash
        ):
            raise RuntimeError("frozen connector identity is no longer available")
        plaintext = decrypt_value(connector.secret_ciphertext)
        try:
            secrets = json.loads(plaintext, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, UnicodeDecodeError, DuplicateKey) as exc:
            raise RuntimeError("connector secret payload is invalid") from exc
        if not isinstance(secrets, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in secrets.items()
        ):
            raise RuntimeError("connector secret payload must be a string map")
        adapter = create_evidence_connector(
            snapshot.connector_kind,
            snapshot.config_masked,
            secrets,
        )
        return adapter


class DuplicateKey(ValueError):
    pass


def _unique_object(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result
